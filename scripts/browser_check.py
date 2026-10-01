"""End-to-end browser proof, including offline/reconnect and measured receipt-to-render."""
import json
import os
import statistics
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
BASE=os.getenv('RAIL_TEST_URL','http://127.0.0.1:8000')
errors=[]
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
    context=browser.new_context(viewport={'width':1512,'height':1100},device_scale_factor=1)
    page=context.new_page()
    page.on('pageerror',lambda e:errors.append(str(e)))
    # External basemap tiles/fonts are optional. The railway geometry and Leaflet are local.
    page.goto(BASE,wait_until='domcontentloaded')
    page.locator('#login-form button').click()
    page.locator('#application').wait_for(state='visible')
    page.wait_for_function('window.railMetrics.events > 0')
    page.locator('#load-demo').click()
    page.wait_for_function("document.querySelectorAll('.train-row').length === 2")
    page.locator('#speed').select_option('120')
    page.locator('#start').click()
    page.wait_for_function("document.querySelector('#sim-clock').textContent >= '00:16:40'",timeout=30000)
    page.locator('#pause').click()
    page.locator('#map-loading').wait_for(state='hidden',timeout=45000)
    page.frame_locator('#rail-map').locator('#base').select_option('none',force=True)
    page.screenshot(path=str(ROOT/'artifacts/dashboard.png'),full_page=True)
    before=page.evaluate('window.railMetrics.events')
    # Actual transport loss; reload is not used to mask reconnect defects.
    context.set_offline(True)
    page.wait_for_function("document.querySelector('#connection').textContent.toLowerCase().includes('disconnect')",timeout=20000)
    context.set_offline(False)
    page.wait_for_function(f'window.railMetrics.events > {before}',timeout=25000)
    page.wait_for_function("document.querySelector('#connection').textContent.includes('Connected')",timeout=15000)
    reconnect=True
    page.locator('[data-tab="incidents"]').click()
    page.locator('#incident-burst').click()
    page.wait_for_function("document.querySelectorAll('.incident-item').length === 10")
    page.locator('.clear-incident').first.click()
    page.wait_for_function("document.querySelectorAll('.incident-item').length === 9")
    page.locator('[data-tab="history"]').click()
    page.locator('#load-replay').click()
    page.locator('#replay-banner').wait_for(state='visible')
    assert page.locator('#start').is_disabled()
    replay=True
    page.locator('#replay-slider').fill('10')
    page.locator('#return-live').click()
    page.locator('#replay-banner').wait_for(state='hidden')
    with page.expect_download() as downloaded:
        page.locator('a[href="/api/report.csv"]').click()
    downloaded.value.save_as(str(ROOT/'artifacts/sample-report.csv'))
    page.locator('[data-tab="timetable"]').click()
    page.screenshot(path=str(ROOT/'artifacts/timetable.png'),full_page=True)
    page.locator('[data-tab="overview"]').click()
    # Client ignores obsolete state instead of rolling back a newer plan.
    version=page.evaluate('lastVersion')
    page.evaluate('receive({...liveState, version:liveState.version-1})')
    assert page.evaluate('lastVersion')>=version
    values=page.evaluate('window.railMetrics')
    metrics=page.request.get(BASE+'/api/metrics').json()
    page.set_viewport_size({'width':390,'height':844})
    page.screenshot(path=str(ROOT/'artifacts/mobile.png'),full_page=True)
    mobile_overflow=page.evaluate('document.documentElement.scrollWidth > innerWidth + 1')
    result={'browser':'Playwright Chromium','viewport':'1512x1100, DPR 1','reconnect_verified':reconnect,'replay_read_only_verified':replay,'report_download_verified':True,'out_of_order_discarded':values['discarded'],'received_events':values['events'],'browser_errors':errors,'mobile_document_overflow':mobile_overflow,'server_metrics':metrics}
    for name,key in [('ui_receipt_to_render_ms','renderMs'),('map_message_to_ack_ms','mapLatencyMs')]:
        a=sorted(values[key]);result[name]={'samples':len(a),'median':round(statistics.median(a),3),'p95':round(a[max(0,int(len(a)*.95)-1)],3),'max':round(max(a),3)} if a else None
    (ROOT/'artifacts/browser-results.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    assert not errors
    assert not mobile_overflow
    assert result['ui_receipt_to_render_ms']['p95']<500
    browser.close()

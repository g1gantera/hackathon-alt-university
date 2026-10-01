"""CSV audit export. Actual observations and timetable forecasts stay separate."""
import csv
import io
import json
from datetime import datetime, timezone


COLUMNS = ('record_type', 'epoch', 'record_id', 'observed_at_utc', 'sim_time_s',
           'state_version', 'basis', 'train_id', 'section_id', 'plan_id',
           'quality_signature', 'metric', 'value', 'unit', 'before', 'after',
           'change', 'event_type', 'details')


def safe_cell(value):
    # Keep numeric negatives numeric; escape text that spreadsheet apps execute.
    if isinstance(value, str) and (value.startswith(('\t', '\r', '\n')) or value.lstrip().startswith(('=', '+', '-', '@'))):
        return "'" + value
    return value


def report_rows(store, window):
    common = {'epoch': window['epoch'], 'basis': 'report_window'}
    for key, value, unit in (
        ('window_start', window['from_s'], 'model_s'), ('window_end', window['to_s'], 'model_s'),
        ('through_record_id', window['through_id'], 'id'), ('saved_snapshots', len(window['frames']), 'count'),
        ('events', len(window['events']), 'count'),
        ('applied_plans', sum(e['type']=='plan.applied' for e in window['events']), 'count'),
    ):
        yield {**common, 'record_type':'summary', 'metric':key, 'value':value, 'unit':unit}
    yield {**common, 'record_type':'summary', 'metric':'sampling',
           'details':'Saved model states only; gaps are not interpolated. Delays and energy are cumulative observations, not interval sums. Conflict rows are repeated observations of timetable violations, not distinct accidents. Replan comparisons are forecasts at application time.'}

    for record in store.archive_records(window):
        data=record.payload
        context={'epoch':record.epoch, 'record_id':record.id, 'sim_time_s':record.sim_time,
                 'observed_at_utc':datetime.fromtimestamp(record.created_at,timezone.utc).isoformat(),
                 'state_version':data.get('state_version')}
        if record.kind=='snapshot':
            metric=data['metrics']
            context.update(plan_id=data.get('active_plan_id'),quality_signature=metric.get('quality_signature'),basis='actual')
            for key,unit in (('index','score_0_100'),('total_delay_s','s'),('max_delay_s','s'),
                             ('energy_kwh','kWh'),('completed_trips','count'),('on_time_pct','percent'),('assessment','text')):
                yield {**context,'record_type':'quality','metric':key,'value':metric.get(key),'unit':unit}
            yield {**context,'record_type':'quality','basis':'schedule_validation','metric':'conflicts',
                   'value':metric.get('conflicts'),'unit':'count'}
            yield {**context,'record_type':'formula','metric':'quality_formula','details':json.dumps(metric.get('formula',{}),ensure_ascii=False)}
            for key,component in metric.get('components',{}).items():
                yield {**context,'record_type':'component','metric':key,'value':component['score'],
                       'unit':'score_0_100','details':json.dumps(component,ensure_ascii=False)}
            for train in data['trains']:
                for key,unit in (('delay_s','s'),('speed_mps','m/s'),('position_m','m'),('energy_kwh','kWh'),('status','text')):
                    yield {**context,'record_type':'train','train_id':train['id'],'section_id':train.get('section_id'),
                           'metric':key,'value':train.get(key),'unit':unit}
            for conflict in data.get('dispatch',{}).get('conflicts',[]):
                yield {**context,'record_type':'conflict','basis':'schedule_validation',
                       'train_id':conflict.get('train_id'),'section_id':conflict.get('section_id'),
                       'metric':conflict.get('code'),'details':json.dumps(conflict,ensure_ascii=False)}
        elif record.kind=='event':
            event_type=data['type']
            payload=data.get('payload',{})
            context.update(event_type=event_type,plan_id=data.get('plan_id'),basis='event')
            # Solver candidate arrays are not useful report rows; application is recorded separately.
            detail={k:v for k,v in payload.items() if k not in ('plans','comparison','plan','profile')}
            yield {**context,'record_type':'event','details':json.dumps(detail,ensure_ascii=False)}
            change=payload.get('comparison')
            if event_type!='plan.applied' or not change:
                continue
            context.update(basis='forecast_at_application',plan_id=change['new_plan_id'])
            before,after=change['before'],change['after']
            detail=json.dumps({'old_plan_id':change['old_plan_id'],'new_plan_id':change['new_plan_id'],
                               'evaluated_at_s':change.get('evaluated_at_s'),
                               'calculation_s':change.get('calculation_s'),
                               'committed_preserved':change['committed_preserved'],
                               'committed_total':change.get('committed_total'),
                               'before_valid':before.get('forecast_valid'),'after_valid':after.get('forecast_valid'),
                               'before_formula':before.get('formula'),'after_formula':after.get('formula')},ensure_ascii=False)
            for key,unit in (('index','score_0_100'),('total_delay_s','s'),('max_delay_s','s'),('energy_kwh','kWh'),('conflicts','count')):
                left,right=before.get(key),after.get(key)
                yield {**context,'record_type':'replan','metric':key,'unit':unit,'before':left,'after':right,
                       'change':round(right-left,4) if left is not None and right is not None else None,'details':detail}
            for train in change.get('trains',[]):
                yield {**context,'record_type':'replan_train','train_id':train['train_id'],'metric':'terminal_arrival',
                       'unit':'model_s','before':train['before_arrival_s'],'after':train['after_arrival_s'],'change':train['change_s']}
            for move in change.get('changes',[]):
                for name,before_key,after_key in (('departure','before_s','after_s'),
                                                ('arrival','before_arrival_s','after_arrival_s'),
                                                ('tail_release','before_release_s','after_release_s')):
                    left,right=move.get(before_key),move.get(after_key)
                    if left is not None and right is not None and left!=right:
                        yield {**context,'record_type':'replan_movement','train_id':move['train_id'],'section_id':move['section_id'],
                               'metric':name,'unit':'model_s','before':left,'after':right,
                               'change':right-left,'details':json.dumps({'leg':move['leg']})}


def csv_report(store, window):
    """UTF-8 BOM opens Cyrillic correctly in Excel; flush small chunks."""
    output=io.StringIO(newline='')
    writer=csv.DictWriter(output,fieldnames=COLUMNS)
    output.write('\ufeff')
    writer.writeheader()
    for row in report_rows(store,window):
        writer.writerow({key:safe_cell(value) for key,value in row.items()})
        if output.tell()>=32768:
            yield output.getvalue()
            output.seek(0);output.truncate(0)
    if output.tell():
        yield output.getvalue()

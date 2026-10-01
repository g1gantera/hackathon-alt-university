import asyncio
import copy

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.simulator import Simulator
from backend.ingestion.client import Ingestion
from backend.ingestion.contracts import Frame, KINDS, merge, split
from backend.ingestion.service import create_app


def test_canonical_roundtrip_and_mixed_revision_rejection():
    snapshot=Simulator().snapshot()
    frames=split(snapshot)
    assert merge(snapshot,frames)==snapshot
    frames[1]=frames[1].model_copy(update={'state_version':999})
    with pytest.raises(ValueError,match='Mixed'):
        merge(snapshot,frames)
    with pytest.raises(ValueError,match='Incomplete'):
        merge(snapshot,frames[:2])


@pytest.mark.parametrize('corrupt',[
    lambda p:p['trains'][0].update(speed_mps=float('nan')),
    lambda p:p['trains'][0].update(coordinate=[200,0]),
    lambda p:p['trains'][0].update(direction=True),
    lambda p:p['trains'].append(copy.deepcopy(p['trains'][0])),
])
def test_movement_ingestion_rejects_malformed_data(corrupt):
    frame=split(Simulator().snapshot())[0].model_dump()
    corrupt(frame['payload'])
    with pytest.raises(ValidationError):
        Frame.model_validate(frame)


@pytest.mark.parametrize('corrupt',[
    lambda p:p['plan']['movements'].__setitem__(0,None),
    lambda p:p['plan']['movements'][0].update(section_id=None),
    lambda p:p.update(active_plan_id=None),
])
def test_timetable_service_returns_validation_errors_for_malformed_entities(corrupt):
    frame=split(Simulator().snapshot())[2].model_dump()
    corrupt(frame['payload'])
    with TestClient(create_app('timetable','service-test-token')) as client:
        response=client.post('/v1/normalize',json=frame,headers={'Authorization':'Bearer service-test-token'})
        assert response.status_code==422


@pytest.mark.parametrize('kind',KINDS)
def test_services_authenticate_and_route_only_their_own_schema(kind):
    with TestClient(create_app(kind,'service-test-token')) as client:
        frames=split(Simulator().snapshot())
        frame=next(frame for frame in frames if frame.kind==kind)
        assert client.post('/v1/normalize',json=frame.model_dump()).status_code==401
        auth={'Authorization':'Bearer service-test-token'}
        response=client.post('/v1/normalize',json=frame.model_dump(),headers=auth)
        assert response.status_code==200 and response.json()==frame.model_dump()
        wrong=next(frame for frame in frames if frame.kind!=kind)
        assert client.post('/v1/normalize',json=wrong.model_dump(),headers=auth).status_code==422
        assert client.get('/health').json()['accepted']==1


def test_remote_delivery_recovers_and_cannot_publish_across_reset():
    async def run():
        snapshot=Simulator().snapshot()
        context=[(snapshot['epoch'],snapshot['state_version'])]
        published=[]
        fail=[False]
        entered=asyncio.Event()
        release=asyncio.Event()
        release.set()
        async def transport(request):
            entered.set()
            await release.wait()
            return httpx.Response(503 if fail[0] else 200,content=request.content)
        bus=Ingestion(published.append,lambda:context[0],mode='remote',token='token',transport=httpx.MockTransport(transport))
        try:
            await bus.start(snapshot)
            assert len(published)==1
            fail[0]=True
            bus.deliver(snapshot)
            async with asyncio.timeout(2):
                while bus.status['failed']==0:await asyncio.sleep(.01)
            assert len(published)==1 and bus.status['status']=='degraded'
            fail[0]=False
            release.clear();entered.clear()
            bus.deliver(snapshot)
            await entered.wait()
            replacement=Simulator().snapshot()
            context[0]=(replacement['epoch'],replacement['state_version'])
            reconnect=asyncio.create_task(bus.current_snapshot())
            bus.deliver(replacement)
            release.set()
            async with asyncio.timeout(2):
                while len(published)<2:await asyncio.sleep(.01)
            assert published[-1]['epoch']==replacement['epoch']
            assert (await reconnect)['epoch']==replacement['epoch']
            assert len(published)==2 and bus.status['status']=='ready'
        finally:
            await bus.close()
    asyncio.run(run())

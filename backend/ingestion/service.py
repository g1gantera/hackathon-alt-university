"""Run once per INGESTION_KIND; each deployment has its own health/schema API."""
import os
import secrets

from fastapi import FastAPI, Header, HTTPException
from .contracts import Frame, KINDS


def create_app(kind=None, token=None):
    kind = kind or os.environ.get('INGESTION_KIND', 'movement')
    token = token if token is not None else os.environ.get('INGESTION_TOKEN', '')
    if kind not in KINDS:
        raise ValueError('Unknown INGESTION_KIND')
    app = FastAPI(title=f'RailFlow {kind} ingestion', version='1.0.0')
    stats = {'accepted': 0}

    @app.get('/health')
    def health():
        return {'status': 'ready' if token else 'unconfigured', 'kind': kind, 'version': 1,
                'accepted': stats['accepted']}

    @app.post('/v1/normalize', response_model=Frame)
    def normalize(frame: Frame, authorization: str = Header(default='')):
        if not token or not secrets.compare_digest(authorization, 'Bearer '+token):
            raise HTTPException(401, 'Service authentication required')
        if frame.kind != kind:
            raise HTTPException(422, 'Frame sent to the wrong ingestion service')
        stats['accepted'] += 1
        return frame

    return app


app = create_app()

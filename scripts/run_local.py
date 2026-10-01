"""Start all four local services. Ctrl+C stops only this supervisor's children."""
import argparse
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import json

from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api-port',type=int,default=8001)
    parser.add_argument('--service-base',type=int,default=8101)
    parser.add_argument('--database',type=Path)
    parser.add_argument('--env-file',type=Path,help='Load configuration; existing environment variables take precedence')
    parser.add_argument('--verify',action='store_true',help='Run incident checks on an explicitly supplied isolated database, then stop')
    args=parser.parse_args()
    if args.env_file and not args.env_file.is_file():
        parser.error('Environment file does not exist')
    if args.verify and not args.database:
        parser.error('--verify requires --database pointing to a disposable test database')
    ports=[args.api_port,*range(args.service_base,args.service_base+3)]
    if len(set(ports))!=4 or any(not 1024<=port<=65535 for port in ports):
        parser.error('Choose four distinct ports between 1024 and 65535')
    for port in ports:
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',port))
    env={**({key:value for key,value in dotenv_values(args.env_file).items() if value is not None} if args.env_file else {}),**os.environ}
    if args.verify:
        env['DEMO_MODE']='true'
    env['INGESTION_MODE']='remote'
    env['INGESTION_TOKEN']=env.get('INGESTION_TOKEN') or secrets.token_urlsafe(32)
    if args.database:
        path=args.database.resolve()
        path.parent.mkdir(parents=True,exist_ok=True)
        env['DATABASE_URL']='sqlite:///'+path.as_posix()
    children=[]

    def launch(module,port,extra):
        child=subprocess.Popen([sys.executable,'-m','uvicorn',module,'--host','127.0.0.1','--port',str(port)],
            cwd=ROOT,env={**env,**extra},start_new_session=os.name!='nt',
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        children.append(child)
        return child

    def ready(port,path):
        deadline=time.monotonic()+40
        while time.monotonic()<deadline:
            if any(child.poll() is not None for child in children):
                raise RuntimeError('A service exited during startup')
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}{path}',timeout=1) as response:
                    if json.load(response)['status']=='ready':
                        return
            except (OSError,ValueError):
                pass
            time.sleep(.2)
        raise RuntimeError(f'Service on port {port} did not become ready')

    try:
        for index,kind in enumerate(('movement','infrastructure','timetable')):
            port=args.service_base+index
            env[f'{kind.upper()}_INGESTION_URL']=f'http://127.0.0.1:{port}'
            launch('backend.ingestion.service:app',port,{'INGESTION_KIND':kind})
        for port in ports[1:]:
            ready(port,'/health')
        launch('backend.app.main:app',args.api_port,{})
        ready(args.api_port,'/api/health')
        print(f'RailFlow ready: http://127.0.0.1:{args.api_port}/ (3 ingestion services)',flush=True)
        if args.verify:
            subprocess.run([sys.executable,str(ROOT/'scripts/smoke_performance.py'),
                f'http://127.0.0.1:{args.api_port}','--repetitions','1'],cwd=ROOT,env=env,check=True)
            return
        while all(child.poll() is None for child in children):
            time.sleep(.5)
        raise RuntimeError('A service stopped; shutting down the remaining services')
    except KeyboardInterrupt:
        pass
    finally:
        for child in reversed(children):
            if child.poll() is not None:
                continue
            if os.name=='nt':
                subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            else:
                os.killpg(child.pid,signal.SIGTERM)
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                if os.name!='nt':
                    os.killpg(child.pid,signal.SIGKILL)
                else:
                    child.kill()


if __name__=='__main__':
    main()

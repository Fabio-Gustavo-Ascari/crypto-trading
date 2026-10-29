"""Command line entry point."""
from pathlib import Path
import argparse
import json
from .core import Store, Broker, Policy
from .market import Market
from .service import run, run_lock, export_report

ROOT=Path(__file__).resolve().parents[2]

def main():
    parser=argparse.ArgumentParser(description='Crypto Harness: manual paper research and simulation')
    parser.add_argument('--data-dir',type=Path,default=ROOT/'data')
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ('start','once'):
        p=sub.add_parser(name)
        p.add_argument('--observe',action='store_true',help='Market data only; zero AI calls')
        p.add_argument('--interval',type=int,default=300)
        p.add_argument('--position-review-interval',type=int,default=60,help='Seconds between reviews of open positions')
        p.add_argument('--max-calls',type=int,default=8)
        p.add_argument('--max-daily-calls',type=int,default=8)
        p.add_argument('--model',default=None,help='Optional supported Codex model name')
        p.add_argument('--max-cycles',type=int,default=None,help='Optional bounded smoke test')
    for name in ('stop','status','report','close'): sub.add_parser(name)
    args=parser.parse_args()
    directory=args.data_dir.resolve()
    try:
        if args.command in ('start','once'):
            if args.interval<30 or args.position_review_interval<30 or not 0<=args.max_calls<=100 or not 0<=args.max_daily_calls<=100 or (args.max_cycles is not None and args.max_cycles<1): parser.error('intervals >=30; calls 0..100; max-cycles >=1')
            run(directory,once=args.command=='once',ai=not args.observe,interval=args.interval,position_review_interval=args.position_review_interval,max_calls=args.max_calls,max_daily_calls=args.max_daily_calls,model=args.model,max_cycles=args.max_cycles)
        elif args.command=='stop':
            directory.mkdir(parents=True,exist_ok=True)
            (directory/'stop.request').touch()
            print('Stop requested. The running process closes paper positions and saves its report; allow up to 2 minutes.')
        elif args.command in ('status','report'):
            if not (directory/'harness.db').exists(): print('No run yet. Use crypto-harness once --observe.'); return
            store=Store(directory/'harness.db')
            if args.command=='report': print(export_report(store,directory))
            else: print(json.dumps(store.report(),indent=2,ensure_ascii=False))
        elif args.command=='close':
            with run_lock(directory):
                store=Store(directory/'harness.db'); market=Market()
                quotes,_,errors=market.collect()
                print(json.dumps(Broker(store,Policy()).protect(quotes,close_all=True),indent=2))
                print('Remaining: '+str(list(store.state()['positions'])))
                export_report(store,directory); market.close()
    except (RuntimeError,OSError,ValueError) as e:
        parser.exit(1,str(e)+'\n')

if __name__=='__main__': main()

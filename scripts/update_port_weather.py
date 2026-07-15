#!/usr/bin/env python3
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from weather.service import update_ports

parser=argparse.ArgumentParser(); parser.add_argument("--port-id",action="append"); parser.add_argument("--force",action="store_true"); parser.add_argument("--dry-run",action="store_true")
args=parser.parse_args(); print(json.dumps(update_ports(args.port_id,args.force,args.dry_run),ensure_ascii=False,indent=2))

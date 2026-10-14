"""Paired frozen-evidence experiment; not a production scan or email run."""
import argparse,copy,hashlib,json,os,time
from pathlib import Path
from dotenv import load_dotenv
import analyzer,config,editorial_policy,scan_runtime
from boundary_policy_trial import BOUNDARY,TECH_INCLUDE,TECH_EXCLUDE

class Audit:
    def __init__(self,path):
        self.path=path;self.model_requests=[];self.failed_review_providers=set()
    def get_decision(self,*args):return None
    def cache_decision(self,*args):pass
    def persist(self):pass
    def save_manifest(self,status,**kwargs):
        self.path.write_text(json.dumps({'status':status,'requests':self.model_requests,**kwargs},indent=2),encoding='utf-8')

def outcome_map(data):
    return {i['url']:{'outcome':i.get('editorial_tier',category),'item':i}
            for category in ('reports','events','podcasts','excluded','needs_review') for i in data[category]}

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--workspace',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--proposed-only',action='store_true')
    args=parser.parse_args()
    root=Path(args.workspace).resolve();out=Path(args.output).resolve()
    load_dotenv(root/'.env',override=True)
    config.OPENROUTER_API_KEY=os.getenv('OPENROUTER_API_KEY')
    assert config.OPENROUTER_API_KEY,'Missing configured review API key'
    protected=[root/'.scanner_state/seen_items.json',root/'.scanner_state/workflow_state.json',root/'.env']
    before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in protected if p.exists()}
    cases=json.loads((out/'boundary-cases.json').read_text(encoding='utf-8'))
    items=[]
    for case in cases:
        row=copy.deepcopy(case['original'])
        # Isolate semantic relevance from historical date/delivery eligibility.
        # Originals remain in the frozen file; these are not newly publishable.
        row.update(date_status='verified_in_window',date_source='page_publication',
                   date_crosscheck_status='',last_reported_run_date='',last_emailed_run_date='',
                   item_type='report',content_type_guess='report',paywall_detected=False)
        items.append(row)
    base_policy=editorial_policy.POLICY
    base_ontology=copy.deepcopy(config.TOPIC_ONTOLOGY)
    results=json.loads((out/'boundary-test-results.json').read_text(encoding='utf-8')) if args.proposed_only else {}
    total_start=time.monotonic()
    for profile in (('proposed',) if args.proposed_only else ('current','proposed')):
        if profile=='proposed':
            editorial_policy.POLICY=base_policy+'\n'+BOUNDARY
            config.TOPIC_ONTOLOGY['Emerging Technologies'].update(include=TECH_INCLUDE,exclude=TECH_EXCLUDE)
        audit=Audit(out/f'{profile}-requests.json')
        started=time.monotonic()
        with scan_runtime.activate(audit):
            data=analyzer.analyze_items(copy.deepcopy(items),'z-ai/glm-5.3-flash')
        elapsed=time.monotonic()-started
        (out/f'{profile}-analysis.json').write_text(json.dumps(data,indent=2,ensure_ascii=False),encoding='utf-8')
        audit.save_manifest('complete',elapsed_seconds=round(elapsed,3))
        requests=audit.model_requests
        results[profile]={'elapsed_seconds':round(elapsed,3),'cost_usd':sum((r.get('usage') or {}).get('cost',0) or 0 for r in requests),
            'requests':len(requests),'providers':sorted({r.get('provider','unknown') for r in requests}),
            'counts':{k:len(data[k]) for k in ('reports','events','podcasts','excluded','needs_review')},
            'metrics':data['analysis_metrics'],'outcomes':outcome_map(data)}
        print(profile,results[profile]['counts'],round(elapsed,3),flush=True)
    after={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in protected if p.exists()}
    assert before==after,'Production protected files changed during isolated test'
    comparison=[]
    for c in cases:
        r=c['original'];url=r.get('canonical_url') or r['url']
        comparison.append({'case_id':c['case_id'],'title':r['title'],'url':url,
            'current':results['current']['outcomes'][url]['outcome'],
            'proposed':results['proposed']['outcomes'][url]['outcome'],
            'proposed_reason':results['proposed']['outcomes'][url]['item'].get('exclusion_reason') or results['proposed']['outcomes'][url]['item'].get('why_it_matters')})
    results['comparison']=comparison
    results['total_seconds']=round(sum(results[k]['elapsed_seconds'] for k in ('current','proposed')),3)
    results['production_state_unchanged']=before==after
    results['fresh_source_scan']=False;results['email_sent']=False
    (out/'boundary-test-results.json').write_text(json.dumps(results,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'total_seconds':results['total_seconds'],'cost_usd':sum(results[k]['cost_usd'] for k in ('current','proposed')),'comparison':comparison},indent=2,ensure_ascii=True))

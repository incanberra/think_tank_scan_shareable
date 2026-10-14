"""Read-only discovery/extraction smoke test; no production history or email."""
import argparse,json,time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import config,source_discovery,content_extractor

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',required=True)
    parser.add_argument('--full-native',action='store_true')
    args=parser.parse_args()
    out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
    config.ENRICHMENT_CACHE_DIR=str(out/'validated-date-cache')
    sources=[s for s in config.DISCOVERY_SOURCES if s['domain'] in {'piie.com','bruegel.org','merics.org','eria.org','iseas.edu.sg'}]
    start=time.monotonic()
    if args.full_native:
        rows,status,audit=source_discovery.discover_native_sources('2026-10-14',source_names=[s['name'] for s in sources])
        (out/'full-native-validation.json').write_text(json.dumps({'candidates':rows,'status':status,'audit':audit,'elapsed_seconds':round(time.monotonic()-start,3)},indent=2,default=str),encoding='utf-8')
        print('Full native discovery:',len(rows),'candidates',flush=True)
    def check(source):
        # A wide diagnostic window tests routes even for less frequent publishers.
        rows,status,rejected=source_discovery.scan_index_pages(source,'2026-10-14',coverage_hours=24*90)
        examples=[]
        for row in rows[:2]:
            page=content_extractor.extract_page(row['url'],force_refresh=True)
            examples.append({'candidate':row,'page':page})
        return {'source':source['name'],'status':status,'candidates':rows,'rejected':rejected,'examples':examples}
    with ThreadPoolExecutor(max_workers=5) as pool:results=list(pool.map(check,sources))
    (out/'source-validation.json').write_text(json.dumps(results,indent=2,ensure_ascii=False,default=str),encoding='utf-8')
    summary={'elapsed_seconds':round(time.monotonic()-start,3),'sources':[{
        'source':r['source'],'status':r['status'],'candidates':len(r['candidates']),
        'examples':[{'url':e['candidate']['url'],'status':e['page'].get('extraction_status'),
         'text_chars':e['page'].get('extracted_text_chars'),
         'publication':str(e['page'].get('extracted_date_dt') or ''),'date_detail':e['page'].get('publication_date_source_detail')} for e in r['examples']]
    } for r in results]}
    (out/'source-validation-summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2))

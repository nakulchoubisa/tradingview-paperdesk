from fastapi.testclient import TestClient
import os
from uuid import uuid4
os.environ.setdefault('DATABASE_URL','sqlite:///./test_papertrade.db')
os.environ['ADMIN_PASSWORD']='test-admin-password-123!'
os.environ['SESSION_SECRET']='test-session-secret-long-enough'
os.environ['WEBHOOK_SECRET']='test-webhook-secret'
from main import app
client=TestClient(app)
def login():
    r=client.post('/api/auth/login',json={'username':'admin','password':'test-admin-password-123!'})
    assert r.status_code==200,r.text
def post(p): return client.post('/api/webhook/tradingview',headers={'X-Webhook-Secret':'test-webhook-secret'},json=p)
def test_health_and_auth():
    assert client.get('/health').json()['database']=='ok'
    client.cookies.clear(); assert client.get('/api/dashboard').status_code==401
    login(); assert client.get('/api/auth/me').status_code==200
def test_webhook_auth_dedup_and_entry():
    assert client.post('/api/webhook/tradingview',json={'event':'ENTRY_LONG','symbol':'NSE:TEST'}).status_code==401
    p={'event':'ENTRY_LONG','event_id':'test-entry','trade_id':'test-trade','strategy':'test-strategy','symbol':'NSE:TEST','entry_price':100,'quantity':2,'stop_loss':90,'target':120}
    assert post(p).status_code==200; assert post(p).json()['duplicate'] is True
    assert any(t['trade_id']=='test-trade' for t in client.get('/api/trades?status=OPEN').json())
def test_price_target_and_short_pnl():
    assert post({'event':'ENTRY_LONG','event_id':'long-target','trade_id':'long-target','strategy':'long-s','symbol':'NSE:TEST','entry_price':100,'quantity':2,'target':120}).status_code==200
    assert post({'event':'PRICE_UPDATE','event_id':'price1','strategy':'MARKET_PRICE_MONITOR','symbol':'NSE:TEST','price':120}).status_code==200
    assert not any(t['trade_id']=='long-target' for t in client.get('/api/trades?status=OPEN').json())
    assert post({'event':'ENTRY_SHORT','event_id':'short-entry','trade_id':'short-trade','strategy':'short-s','symbol':'NSE:SHORT','entry_price':200,'quantity':3}).status_code==200
    assert post({'event':'PRICE_UPDATE','event_id':'price-short','strategy':'m','symbol':'NSE:SHORT','price':190}).status_code==200
    t=next(x for x in client.get('/api/trades?status=OPEN').json() if x['trade_id']=='short-trade'); assert t['unrealized_pnl']==30

def test_five_targets_take_equal_slices_and_trail_stop():
    tid='five-target-long'; levels=[110,120,130,140,150]
    opened=post({'event':'ENTRY_LONG','event_id':'five-target-entry','trade_id':tid,'strategy':'staged','symbol':'NSE:STAGED','entry_price':100,'quantity':10,'stop_loss':90,'target':150,'targets':levels})
    assert opened.status_code==200,opened.text
    assert post({'event':'PRICE_UPDATE','event_id':'five-target-p1','strategy':'monitor','symbol':'NSE:STAGED','price':115}).status_code==200
    trade=next(x for x in client.get('/api/trades').json() if x['trade_id']==tid)
    assert trade['quantity']==8 and trade['stop_loss']==100 and trade['target_stage']==1 and trade['targets']==levels
    assert any(x['side']=='SL_MOVE' and x['price']==100 for x in trade['executions'])
    assert post({'event':'PRICE_UPDATE','event_id':'five-target-p3','strategy':'monitor','symbol':'NSE:STAGED','price':135}).status_code==200
    trade=next(x for x in client.get('/api/trades').json() if x['trade_id']==tid)
    assert trade['quantity']==4 and trade['stop_loss']==120 and trade['target_stage']==3
    assert post({'event':'PRICE_UPDATE','event_id':'five-target-p5','strategy':'monitor','symbol':'NSE:STAGED','price':151}).status_code==200
    trade=next(x for x in client.get('/api/trades').json() if x['trade_id']==tid)
    assert trade['status']=='TARGET_HIT' and trade['exit_reason']=='TP5' and trade['realized_pnl']==300

def test_five_target_short_and_breakeven_trail_stop():
    tid='five-target-short'; levels=[90,80,70,60,50]
    assert post({'event':'ENTRY_SHORT','event_id':'five-target-short-entry','trade_id':tid,'strategy':'staged','symbol':'NSE:STAGED-S','entry_price':100,'quantity':5,'stop_loss':110,'target':50,'targets':levels}).status_code==200
    assert post({'event':'PRICE_UPDATE','event_id':'five-target-short-p1','strategy':'monitor','symbol':'NSE:STAGED-S','price':89}).status_code==200
    trade=next(x for x in client.get('/api/trades').json() if x['trade_id']==tid)
    assert trade['quantity']==4 and trade['stop_loss']==100 and trade['target_stage']==1
    assert post({'event':'PRICE_UPDATE','event_id':'five-target-short-stop','strategy':'monitor','symbol':'NSE:STAGED-S','price':100}).status_code==200
    trade=next(x for x in client.get('/api/trades').json() if x['trade_id']==tid)
    assert trade['status']=='STOP_LOSS_HIT' and trade['exit_reason']=='STOP_LOSS' and trade['realized_pnl']==10

def test_price_lookup_uses_symbol_and_dashboard_serializes_trades():
    suffix=uuid4().hex[:10]; symbol=f'NSE:PRICE-{suffix}'
    assert post({'event':'PRICE_UPDATE','event_id':f'price-first-{suffix}','strategy':'monitor','symbol':symbol,'price':100}).status_code==200
    assert post({'event':'PRICE_UPDATE','event_id':f'price-second-{suffix}','strategy':'monitor','symbol':symbol,'price':101}).status_code==200
    assert post({'event':'ENTRY_LONG','event_id':f'price-entry-{suffix}','trade_id':f'price-trade-{suffix}','strategy':'regression','symbol':symbol,'price':101,'quantity':1}).status_code==200
    login()
    dashboard=client.get('/api/dashboard')
    assert dashboard.status_code==200,dashboard.text
    data=dashboard.json()
    assert data['total_trades']>=1 and data['open_positions']>=1
    trade=next(t for t in client.get('/api/trades?status=OPEN').json() if t['trade_id']==f'price-trade-{suffix}')
    assert trade['price_updated_at'] is not None

def test_price_update_event_log_keeps_latest_snapshot_per_symbol():
    suffix=uuid4().hex[:8]; symbol=f'NSE:COALESCE-{suffix}'
    assert post({'event':'PRICE_UPDATE','event_id':f'price-a-{suffix}','strategy':'monitor','symbol':symbol,'price':100}).status_code==200
    assert post({'event':'PRICE_UPDATE','event_id':f'price-b-{suffix}','strategy':'monitor','symbol':symbol,'price':101}).status_code==200
    rows=[x for x in client.get('/api/events').json() if x['symbol']==symbol and x['event']=='PRICE_UPDATE']
    assert len(rows)==1 and rows[0]['event_id']==f'price-b-{suffix}' and rows[0]['payload']['price']==101
def test_partial_exit_stop_mod_and_reports():
    assert post({'event':'ENTRY_LONG','event_id':'p-entry','trade_id':'partial','strategy':'p','symbol':'NSE:P','price':10,'quantity':4}).status_code==200
    assert post({'event':'MODIFY_SL','event_id':'p-sl','trade_id':'partial','strategy':'p','symbol':'NSE:P','price':8}).status_code==200
    assert post({'event':'EXIT_LONG','event_id':'p-exit','trade_id':'partial','strategy':'p','symbol':'NSE:P','exit_price':12,'exit_quantity':1}).status_code==200
    t=next(x for x in client.get('/api/trades?status=PARTIALLY_CLOSED').json() if x['trade_id']=='partial'); assert t['quantity']==3 and t['realized_pnl']==2 and t['stop_loss']==8
    assert 'strategies' in client.get('/api/reports').json()

def test_body_secret_redacted_and_persisted():
    p={'event':'ENTRY_LONG','event_id':'body-secret-entry','trade_id':'persisted','strategy':'body','symbol':'NSE:PERSIST','price':10,'quantity':1,'timeframe':'1','timestamp':'1761972000000','source':'TRADINGVIEW','secret':'test-webhook-secret'}
    assert client.post('/api/webhook/tradingview',json=p).status_code==200
    from main import SessionLocal,Trade
    with SessionLocal() as s: assert s.query(Trade).filter_by(trade_id='persisted').one().entry_price==10
    logged=next(x for x in client.get('/api/events').json() if x['event_id']=='body-secret-entry')
    assert 'secret' not in logged['payload']

def test_configured_script_allowlist_blocks_unknown_entries():
    login()
    csrf={'X-CSRF-Token':client.cookies.get('paper_csrf')}
    saved=client.put('/api/settings',headers=csrf,json={'total_capital':250000,'max_trades_per_day':0,'script_allowlist_enabled':True})
    assert saved.status_code==200,saved.text
    settings=client.get('/api/settings').json()
    assert settings['total_capital']==250000 and settings['script_allowlist_enabled'] is True
    denied=post({'event':'ENTRY_LONG','event_id':'blocked-script','trade_id':'blocked-script','strategy':'not-allowed','symbol':'NSE:ALLOW','price':100})
    assert denied.status_code==422 and 'allowlist' in denied.json()['detail']
    assert client.post('/api/strategies',headers=csrf,json={'strategy_id':'allowed-script','name':'Allowed script'}).status_code==200
    accepted=post({'event':'ENTRY_LONG','event_id':'accepted-script','trade_id':'accepted-script','strategy':'allowed-script','symbol':'NSE:ALLOW','price':100})
    assert accepted.status_code==200,accepted.text
    assert client.put('/api/settings',headers=csrf,json={'script_allowlist_enabled':False}).status_code==200

def test_legacy_nested_tradingview_payload():
    strategy='ZERO_POINT_AUTOMATION'; symbol='NSE:LEGACY-OPTION'; tid='legacy-paper-trade'
    entry={'payload':{'secret':'test-webhook-secret','event':'ENTRY','event_id':'legacy-entry','strategy':strategy,'symbol':symbol,'side':'SHORT','trade_id':tid,'quantity':2,'fill_price':100,'stop_loss':105,'target':90,'event_time':1761972000000}}
    opened=post(entry)
    assert opened.status_code==200,opened.text
    trade=next(t for t in client.get('/api/trades?status=OPEN').json() if t['trade_id']==tid)
    assert trade['direction']=='SHORT' and trade['entry_price']==100
    exit_event={'payload':{'secret':'test-webhook-secret','event':'EXIT','event_id':'legacy-stop','strategy':strategy,'symbol':symbol,'side':'SHORT','trade_id':tid,'order_id':'STOP_LOSS','quantity':2,'fill_price':106,'event_time':1761972060000}}
    closed=post(exit_event)
    assert closed.status_code==200,closed.text
    trade=next(t for t in client.get('/api/trades').json() if t['trade_id']==tid)
    assert trade['status']=='STOP_LOSS_HIT' and trade['exit_price']==106

def test_bad_and_unmatched_events_are_recorded():
    r=post({'event':'EXIT_LONG','event_id':'unmatched','strategy':'none','symbol':'NSE:MISS','price':1})
    assert r.status_code==422
    assert any(x['event_id']=='unmatched' and x['status']=='FAILED' for x in client.get('/api/events').json())
def test_csrf_protection():
    login()
    assert client.post('/api/settings',json={}).status_code==403

def test_strategy_isolation_and_stop_hit():
    assert post({'event':'ENTRY_LONG','event_id':'isolated-entry','trade_id':'isolated','strategy':'alpha','symbol':'NSE:ISO','price':50,'quantity':1}).status_code==200
    assert post({'event':'EXIT_LONG','event_id':'isolated-exit','strategy':'beta','symbol':'NSE:ISO','exit_price':60}).status_code==422
    assert any(t['trade_id']=='isolated' for t in client.get('/api/trades?status=OPEN').json())
    assert post({'event':'ENTRY_SHORT','event_id':'stop-entry','trade_id':'stop','strategy':'gamma','symbol':'NSE:STOP','price':100,'quantity':1,'stop_loss':105}).status_code==200
    assert post({'event':'PRICE_UPDATE','event_id':'stop-price','strategy':'monitor','symbol':'NSE:STOP','price':106}).status_code==200
    stopped=next(t for t in client.get('/api/trades').json() if t['trade_id']=='stop')
    assert stopped['status']=='STOP_LOSS_HIT' and stopped['exit_reason']=='STOP_LOSS'

def test_strategy_terminal_close_after_price_monitor_is_idempotent():
    strategy='ZERO_POINT_AUTOMATION'; tid='zero-point-idempotent-test'
    assert post({'event':'ENTRY_LONG','event_id':'zero-point-test-entry','trade_id':tid,'strategy':strategy,'symbol':'NSE:ZPTEST','entry_price':100,'quantity':1,'stop_loss':95,'target':110}).status_code==200
    assert post({'event':'PRICE_UPDATE','event_id':'zero-point-test-price','strategy':'MARKET_PRICE_MONITOR','symbol':'NSE:ZPTEST','price':95}).status_code==200
    duplicate_close=post({'event':'STOP_LOSS','event_id':'zero-point-test-strategy-stop','trade_id':tid,'strategy':strategy,'symbol':'NSE:ZPTEST','exit_price':95,'exit_quantity':1})
    assert duplicate_close.status_code==200 and duplicate_close.json()['status']=='PROCESSED'
    login()
    stopped=next(t for t in client.get('/api/trades').json() if t['trade_id']==tid)
    assert stopped['status']=='STOP_LOSS_HIT'

def test_duplicate_count_and_monitor_totals():
    p={'event':'ENTRY_LONG','event_id':'dup-count','trade_id':'dup-count','strategy':'dups','symbol':'NSE:DUP','price':1}
    assert post(p).status_code==200
    assert post(p).json()['duplicate_count']==1
    summary=client.get('/api/events/summary').json()
    assert summary['duplicates']==1 and summary['successful']==1

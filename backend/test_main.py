from fastapi.testclient import TestClient
import os
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

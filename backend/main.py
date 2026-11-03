from __future__ import annotations
import csv, hashlib, io, json, logging, os, secrets, time, math
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Any
from fastapi import FastAPI, Depends, HTTPException, Request, Response, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import create_engine, String, Float, Boolean, DateTime, ForeignKey, Text, Index, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, Session
from sqlalchemy.types import JSON

logging.basicConfig(level=os.getenv('LOG_LEVEL','INFO'))
log=logging.getLogger('papertrade')
DATABASE_URL=os.getenv('DATABASE_URL','sqlite:///./papertrade.db')
# Render and several other hosts expose Postgres URLs with the generic
# postgresql:// scheme. Pin SQLAlchemy to the psycopg v3 driver we install.
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL='postgresql+psycopg://' + DATABASE_URL[len('postgres://'):]
elif DATABASE_URL.startswith('postgresql://'):
    DATABASE_URL='postgresql+psycopg://' + DATABASE_URL[len('postgresql://'):]
connect_args={'check_same_thread':False} if DATABASE_URL.startswith('sqlite') else {}
engine=create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal=sessionmaker(bind=engine, expire_on_commit=False)
class Base(DeclarativeBase): pass
class User(Base):
    __tablename__='users'; id:Mapped[int]=mapped_column(primary_key=True); username:Mapped[str]=mapped_column(String(100),unique=True); password_hash:Mapped[str]=mapped_column(String(255)); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
class Strategy(Base):
    __tablename__='strategies'; id:Mapped[int]=mapped_column(primary_key=True); strategy_id:Mapped[str]=mapped_column(String(120),unique=True,index=True); name:Mapped[str]=mapped_column(String(160)); description:Mapped[str]=mapped_column(Text,default=''); script_name:Mapped[str]=mapped_column(String(160),default=''); symbols:Mapped[list]=mapped_column(JSON,default=list); timeframe:Mapped[str]=mapped_column(String(30),default='1'); enabled:Mapped[bool]=mapped_column(Boolean,default=True); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
class Event(Base):
    __tablename__='webhook_events'; id:Mapped[int]=mapped_column(primary_key=True); event_id:Mapped[str]=mapped_column(String(180),unique=True,index=True); event_type:Mapped[str]=mapped_column(String(40),index=True); strategy_id:Mapped[str]=mapped_column(String(120),default=''); symbol:Mapped[str]=mapped_column(String(120),default=''); received_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc)); status:Mapped[str]=mapped_column(String(30),default='RECEIVED'); duplicate_count:Mapped[int]=mapped_column(default=0); error:Mapped[str]=mapped_column(Text,default=''); payload:Mapped[dict]=mapped_column(JSON,default=dict)
class Price(Base):
    __tablename__='market_prices'; id:Mapped[int]=mapped_column(primary_key=True); symbol:Mapped[str]=mapped_column(String(120),unique=True,index=True); price:Mapped[float]=mapped_column(Float); source:Mapped[str]=mapped_column(String(40),default='TRADINGVIEW'); timeframe:Mapped[str]=mapped_column(String(30),default='1'); updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
class Trade(Base):
    __tablename__='trades'; id:Mapped[int]=mapped_column(primary_key=True); trade_id:Mapped[str]=mapped_column(String(180),unique=True,index=True); strategy_id:Mapped[str]=mapped_column(String(120),index=True); symbol:Mapped[str]=mapped_column(String(120),index=True); direction:Mapped[str]=mapped_column(String(8)); quantity:Mapped[float]=mapped_column(Float); entry_price:Mapped[float]=mapped_column(Float); current_price:Mapped[float]=mapped_column(Float); stop_loss:Mapped[float|None]=mapped_column(Float,nullable=True); target:Mapped[float|None]=mapped_column(Float,nullable=True); status:Mapped[str]=mapped_column(String(30),index=True,default='OPEN'); entry_time:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc)); exit_time:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True); exit_price:Mapped[float|None]=mapped_column(Float,nullable=True); exit_reason:Mapped[str]=mapped_column(String(40),default=''); gross_pnl:Mapped[float]=mapped_column(Float,default=0); charges:Mapped[float]=mapped_column(Float,default=0); net_pnl:Mapped[float]=mapped_column(Float,default=0); mfe:Mapped[float]=mapped_column(Float,default=0); mae:Mapped[float]=mapped_column(Float,default=0); executions:Mapped[list]=mapped_column(JSON,default=list)
Index('ix_trades_open_symbol','trades.status','trades.symbol')
class Audit(Base):
    __tablename__='audit_logs'; id:Mapped[int]=mapped_column(primary_key=True); action:Mapped[str]=mapped_column(String(80)); subject:Mapped[str]=mapped_column(String(180)); at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc)); details:Mapped[dict]=mapped_column(JSON,default=dict)
class Setting(Base):
    __tablename__='application_settings'; key:Mapped[str]=mapped_column(String(100),primary_key=True); value:Mapped[str]=mapped_column(String(500))
class Fill(Base):
    __tablename__='trade_executions'; id:Mapped[int]=mapped_column(primary_key=True); trade_id:Mapped[str]=mapped_column(String(180),index=True); event_id:Mapped[str]=mapped_column(String(180)); side:Mapped[str]=mapped_column(String(12)); quantity:Mapped[float]=mapped_column(Float); price:Mapped[float]=mapped_column(Float); at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
Base.metadata.create_all(engine)

def db():
    s=SessionLocal()
    try: yield s
    finally: s.close()
ph=PasswordHasher(); serializer=URLSafeTimedSerializer(os.getenv('SESSION_SECRET') or secrets.token_urlsafe(32))
COOKIE='paper_session'; CSRF='paper_csrf'
def ensure_admin():
    with SessionLocal() as s:
        if not s.query(User).first():
            username=os.getenv('ADMIN_USERNAME','admin'); password=os.getenv('ADMIN_PASSWORD','')
            if password: s.add(User(username=username,password_hash=ph.hash(password))); s.commit()
ensure_admin()
app=FastAPI(title='Paper Trading Intelligence',version='1.0.0')
app.add_middleware(CORSMiddleware,allow_origins=os.getenv('CORS_ORIGINS','http://localhost:5173').split(','),allow_credentials=True,allow_methods=['*'],allow_headers=['*'])
LOGIN_TRIES={}
def current_user(request:Request,s:Session=Depends(db)):
    token=request.cookies.get(COOKIE)
    if not token: raise HTTPException(401,'Authentication required')
    try: uid=serializer.loads(token,max_age=43200)
    except (BadSignature,SignatureExpired): raise HTTPException(401,'Session expired')
    user=s.get(User,uid)
    if not user: raise HTTPException(401,'Invalid session')
    return user
class Login(BaseModel): username:str; password:str
class EventPayload(BaseModel):
    model_config=ConfigDict(extra='allow')
    event:str; strategy:str=''; symbol:str; price:float|None=Field(default=None,gt=0); timestamp:str|None=None; timeframe:str=''; source:str='TRADINGVIEW'; secret:str|None=None; event_id:str|None=None; trade_id:str|None=None; direction:str|None=None; quantity:float|None=Field(default=None,gt=0); entry_price:float|None=Field(default=None,gt=0); stop_loss:float|None=Field(default=None,gt=0); target:float|None=Field(default=None,gt=0); exit_price:float|None=Field(default=None,gt=0); exit_quantity:float|None=Field(default=None,gt=0)

def setting(s,key,default):
    row=s.get(Setting,key); return float(row.value) if row else default
def pnl(t,price): return (price-t.entry_price)*t.quantity*(1 if t.direction=='LONG' else -1)
def close_trade(s,t,price,reason,qty=None,event_id=''):
    if price is None or price<=0: raise ValueError('exit price must be positive')
    if qty is not None and qty<=0: raise ValueError('exit quantity must be positive')
    q=min(t.quantity,qty if qty is not None else t.quantity)
    gross=(price-t.entry_price)*q*(1 if t.direction=='LONG' else -1); charges=setting(s,'charge_per_unit',0)*q
    t.gross_pnl+=gross; t.charges+=charges; t.net_pnl=t.gross_pnl-t.charges
    t.executions=list(t.executions or [])+ [{'side':'EXIT','quantity':q,'price':price,'gross':gross,'charges':charges,'net':gross-charges,'at':datetime.now(timezone.utc).isoformat()}]
    s.add(Fill(trade_id=t.trade_id,event_id=event_id,side='EXIT',quantity=q,price=price))
    t.quantity-=q; t.current_price=price
    if t.quantity>1e-9: t.status='PARTIALLY_CLOSED'
    if t.quantity<=1e-9:
        t.quantity=0; t.exit_price=price; t.exit_time=datetime.now(timezone.utc); t.exit_reason=reason; t.status={'STOP_LOSS':'STOP_LOSS_HIT','TARGET_HIT':'TARGET_HIT'}.get(reason,'CLOSED')

def process(s,p,event_id):
    typ=p.event.upper(); sid=p.strategy; sym=p.symbol
    if typ=='PRICE_UPDATE':
        if p.price is None or p.price<=0: raise ValueError('price must be positive')
        row=s.get(Price,sym)
        if row: row.price=p.price; row.updated_at=datetime.now(timezone.utc); row.source=p.source; row.timeframe=p.timeframe
        else: s.add(Price(symbol=sym,price=p.price,source=p.source,timeframe=p.timeframe))
        for t in s.query(Trade).filter(Trade.symbol==sym,Trade.status.in_(['OPEN','PARTIALLY_CLOSED'])):
            t.current_price=p.price; up=pnl(t,p.price); t.mfe=max(t.mfe,up); t.mae=min(t.mae,up)
            hit=(t.stop_loss is not None and ((t.direction=='LONG' and p.price<=t.stop_loss) or (t.direction=='SHORT' and p.price>=t.stop_loss)))
            target=(t.target is not None and ((t.direction=='LONG' and p.price>=t.target) or (t.direction=='SHORT' and p.price<=t.target)))
            if hit: close_trade(s,t,p.price,'STOP_LOSS',event_id=event_id)
            elif target: close_trade(s,t,p.price,'TARGET_HIT',event_id=event_id)
        return
    if typ in ('ENTRY_LONG','ENTRY_SHORT'):
        direction='LONG' if typ=='ENTRY_LONG' else 'SHORT'; price=p.entry_price or p.price
        if not price or price<=0: raise ValueError('entry_price or price required')
        tid=p.trade_id or event_id
        if s.query(Trade).filter_by(trade_id=tid).first(): return
        q=p.quantity or setting(s,'default_quantity',1)
        if q<=0: raise ValueError('quantity must be positive')
        s.add(Trade(trade_id=tid,strategy_id=sid,symbol=sym,direction=direction,quantity=q,entry_price=price,current_price=price,stop_loss=p.stop_loss,target=p.target,executions=[{'side':'ENTRY','quantity':q,'price':price,'at':datetime.now(timezone.utc).isoformat()}]))
        s.add(Fill(trade_id=tid,event_id=event_id,side='ENTRY',quantity=q,price=price)); return
    candidates=s.query(Trade).filter(Trade.strategy_id==sid,Trade.symbol==sym,Trade.status.in_(['OPEN','PARTIALLY_CLOSED']))
    if p.trade_id: candidates=candidates.filter(Trade.trade_id==p.trade_id)
    if typ in ('EXIT_LONG','EXIT_SHORT','CLOSE_POSITION','STOP_LOSS','TARGET_HIT'):
        expected='LONG' if typ=='EXIT_LONG' else ('SHORT' if typ=='EXIT_SHORT' else None)
        if expected: candidates=candidates.filter(Trade.direction==expected)
        matches=candidates.all()
        if not matches and p.trade_id:
            # A price-monitor update and a strategy alert can observe the same
            # stop/target on adjacent feeds. Treat a later terminal close for
            # that exact trade as already handled instead of recording a false
            # unmatched-exit failure.
            closed=s.query(Trade).filter_by(trade_id=p.trade_id,strategy_id=sid,symbol=sym).first()
            if closed and closed.status not in ('OPEN','PARTIALLY_CLOSED'):
                return
        if len(matches)!=1: raise ValueError('unmatched or ambiguous exit; specify trade_id')
        t=matches[0]; close_trade(s,t,p.exit_price or p.price or t.current_price,typ,p.exit_quantity,event_id); return
    if typ in ('MODIFY_SL','MODIFY_TARGET'):
        matches=candidates.all()
        if len(matches)!=1: raise ValueError('unmatched or ambiguous modification; specify trade_id')
        t=matches[0]
        if p.price is None or p.price<=0: raise ValueError('positive price required for modification')
        if typ=='MODIFY_SL': t.stop_loss=p.price
        else: t.target=p.price
        s.add(Audit(action=typ,subject=t.trade_id,details={'value':p.price,'event_id':event_id})); return
    raise ValueError('unsupported event')
@app.get('/health')
def health(s:Session=Depends(db)):
    try:
        s.query(User.id).limit(1).all(); prices=s.query(Price).all(); fresh=max([x.updated_at for x in prices],default=None); age=(datetime.now(timezone.utc)-fresh.replace(tzinfo=timezone.utc) if fresh and fresh.tzinfo is None else datetime.now(timezone.utc)-fresh) if fresh else None
        return {'status':'ok','database':'ok','worker':'inline','market_data_freshness':'LIVE' if age and age.total_seconds()<180 else ('STALE' if fresh else 'DISCONNECTED'),'last_price_update':fresh.isoformat() if fresh else None}
    except Exception: raise HTTPException(503,'database unavailable')
@app.middleware('http')
async def csrf_guard(request:Request, call_next):
    if request.method in ('POST','PUT','PATCH','DELETE') and request.url.path.startswith('/api/') and request.url.path not in ('/api/auth/login','/api/webhook/tradingview') and request.cookies.get(COOKIE):
        if not request.cookies.get(CSRF) or not secrets.compare_digest(request.cookies.get(CSRF,''),request.headers.get('x-csrf-token','')):
            return Response('CSRF token missing or invalid',status_code=403)
    return await call_next(request)
@app.post('/api/auth/login')
def login(body:Login,request:Request,response:Response,s:Session=Depends(db)):
    ip=request.client.host if request.client else 'unknown'; now=time.time(); tries=[t for t in LOGIN_TRIES.get(ip,[]) if now-t<900]
    if len(tries)>=10: raise HTTPException(429,'Too many login attempts')
    u=s.query(User).filter_by(username=body.username).first()
    try: ok=u and ph.verify(u.password_hash,body.password)
    except VerifyMismatchError: ok=False
    if not ok: LOGIN_TRIES[ip]=tries+[now]; raise HTTPException(401,'Invalid credentials')
    LOGIN_TRIES.pop(ip,None); secure=os.getenv('COOKIE_SECURE','false').lower()=='true'; response.set_cookie(COOKIE,serializer.dumps(u.id),httponly=True,secure=secure,samesite='strict',max_age=43200); response.set_cookie(CSRF,secrets.token_urlsafe(32),httponly=False,secure=secure,samesite='strict',max_age=43200); return {'username':u.username}
@app.post('/api/auth/logout')
def logout(response:Response): response.delete_cookie(COOKIE); response.delete_cookie(CSRF); return {'ok':True}
@app.get('/api/auth/me')
def me(u:User=Depends(current_user)): return {'username':u.username}
@app.post('/api/webhook/tradingview')
def webhook(payload:EventPayload,request:Request,x_webhook_secret:str|None=Header(None),s:Session=Depends(db)):
    expected=os.getenv('WEBHOOK_SECRET','')
    provided=x_webhook_secret or payload.secret
    if not expected or not provided or not secrets.compare_digest(expected,provided): raise HTTPException(401,'Invalid webhook credential')
    eid=payload.event_id or hashlib.sha256((payload.model_dump_json()+str(payload.timestamp)).encode()).hexdigest()
    existing=s.query(Event).filter_by(event_id=eid).first()
    if existing:
        existing.duplicate_count+=1; s.commit()
        return {'accepted':True,'duplicate':True,'event_id':eid,'duplicate_count':existing.duplicate_count}
    safe_payload=payload.model_dump(mode='json'); safe_payload.pop('secret',None)
    ev=Event(event_id=eid,event_type=payload.event.upper(),strategy_id=payload.strategy,symbol=payload.symbol,payload=safe_payload)
    s.add(ev)
    try:
        process(s,payload,eid); ev.status='PROCESSED'; s.commit()
    except IntegrityError:
        s.rollback()
        existing=s.query(Event).filter_by(event_id=eid).first()
        if existing:
            existing.duplicate_count+=1; s.commit()
            return {'accepted':True,'duplicate':True,'event_id':eid,'duplicate_count':existing.duplicate_count}
        raise HTTPException(409,'Conflicting event')
    except Exception as e:
        s.rollback()
        ev=Event(event_id=eid,event_type=payload.event.upper(),strategy_id=payload.strategy,symbol=payload.symbol,payload=safe_payload,status='FAILED',error=str(e))
        s.add(ev); s.commit()
        raise HTTPException(422,str(e))
    return {'accepted':True,'event_id':eid,'status':'PROCESSED'}
@app.post('/api/simulate')
def simulate(payload:EventPayload,s:Session=Depends(db),u:User=Depends(current_user)):
    eid=payload.event_id or 'sim-'+secrets.token_hex(8); process(s,payload,eid); safe_payload=payload.model_dump(mode='json'); safe_payload.pop('secret',None); s.add(Event(event_id=eid,event_type=payload.event.upper(),strategy_id=payload.strategy,symbol=payload.symbol,status='PROCESSED',payload=safe_payload)); s.commit(); return {'event_id':eid,'status':'PROCESSED'}
def trade_json(t,s):
    un=pnl(t,t.current_price) if t.status in ('OPEN','PARTIALLY_CLOSED') else 0
    return {'trade_id':t.trade_id,'strategy':t.strategy_id,'symbol':t.symbol,'direction':t.direction,'quantity':t.quantity,'entry_price':t.entry_price,'current_price':t.current_price,'stop_loss':t.stop_loss,'target':t.target,'status':t.status,'entry_time':t.entry_time.isoformat(),'exit_time':t.exit_time.isoformat() if t.exit_time else None,'exit_price':t.exit_price,'gross_pnl':t.gross_pnl+un,'realized_pnl':t.gross_pnl,'unrealized_pnl':un,'charges':t.charges,'net_pnl':t.net_pnl+un,'exit_reason':t.exit_reason,'mfe':t.mfe,'mae':t.mae,'price_updated_at':(s.get(Price,t.symbol).updated_at.isoformat() if s.get(Price,t.symbol) else None)}
@app.get('/api/dashboard')
def dashboard(s:Session=Depends(db),u:User=Depends(current_user)):
    trades=s.query(Trade).all(); today=datetime.now(timezone.utc).date(); open_t=[t for t in trades if t.status in ('OPEN','PARTIALLY_CLOSED')]; closed=[t for t in trades if t.exit_time and t.exit_time.date()==today]; realized=sum(t.gross_pnl-t.charges for t in trades); daily=sum(float(ex.get('net',0)) for t in trades for ex in (t.executions or []) if ex.get('side')=='EXIT' and datetime.fromisoformat(ex['at']).date()==today); unreal=sum(pnl(t,t.current_price) for t in open_t)
    return {'today_pnl':daily,'total_realized_pnl':realized,'unrealized_pnl':unreal,'open_positions':len(open_t),'total_trades':len(trades),'win_rate':(100*sum(t.net_pnl>0 for t in trades if t.exit_time)/max(1,sum(bool(t.exit_time) for t in trades))),'open_trades':[trade_json(t,s) for t in open_t],'closed_trades':[trade_json(t,s) for t in sorted(trades,key=lambda x:x.entry_time,reverse=True) if t.exit_time][:30],'events':[{'event_id':e.event_id,'event':e.event_type,'symbol':e.symbol,'status':e.status,'duplicate_count':e.duplicate_count,'received_at':e.received_at.isoformat(),'error':e.error} for e in s.query(Event).order_by(Event.id.desc()).limit(10)],'prices':[{'symbol':p.symbol,'price':p.price,'updated_at':p.updated_at.isoformat(),'status':'LIVE' if (datetime.now(timezone.utc)-(p.updated_at.replace(tzinfo=timezone.utc) if p.updated_at.tzinfo is None else p.updated_at)).total_seconds()<180 else 'STALE','source':p.source} for p in s.query(Price).all()]}
@app.get('/api/trades')
def trades(status:str|None=None,strategy:str|None=None,symbol:str|None=None,s:Session=Depends(db),u:User=Depends(current_user)):
    q=s.query(Trade)
    if status:q=q.filter(Trade.status==status.upper())
    if strategy:q=q.filter(Trade.strategy_id==strategy)
    if symbol:q=q.filter(Trade.symbol==symbol)
    return [trade_json(t,s) for t in q.order_by(Trade.id.desc()).all()]
@app.post('/api/trades/{trade_id}/close')
def manual_close(trade_id:str,s:Session=Depends(db),u:User=Depends(current_user)):
    t=s.query(Trade).filter_by(trade_id=trade_id).first()
    if not t or t.status not in ('OPEN','PARTIALLY_CLOSED'): raise HTTPException(404,'Open trade not found')
    close_trade(s,t,t.current_price,'MANUAL_ADMIN'); s.add(Audit(action='MANUAL_CLOSE',subject=trade_id,details={'user':u.username})); s.commit(); return trade_json(t,s)
@app.get('/api/strategies')
def strategies(s:Session=Depends(db),u:User=Depends(current_user)): return [{'strategy_id':x.strategy_id,'name':x.name,'description':x.description,'script_name':x.script_name,'symbols':x.symbols,'timeframe':x.timeframe,'enabled':x.enabled,'created_at':x.created_at.isoformat()} for x in s.query(Strategy).all()]
@app.post('/api/strategies')
def add_strategy(body:dict,s:Session=Depends(db),u:User=Depends(current_user)):
    if not body.get('strategy_id') or not body.get('name'): raise HTTPException(422,'strategy_id and name required')
    row=s.query(Strategy).filter_by(strategy_id=body['strategy_id']).first()
    if not row: row=Strategy(strategy_id=body['strategy_id'],name=body['name']); s.add(row)
    for k in ('name','description','script_name','symbols','timeframe','enabled'):
        if k in body:setattr(row,k,body[k])
    s.commit(); return {'ok':True}
@app.get('/api/events/summary')
def event_summary(s:Session=Depends(db),u:User=Depends(current_user)):
    rows=s.query(Event).all()
    return {'total':len(rows),'successful':sum(e.status=='PROCESSED' for e in rows),'failed':sum(e.status=='FAILED' for e in rows),'duplicates':sum(e.duplicate_count for e in rows),'last_received':max((e.received_at.isoformat() for e in rows),default=None)}
@app.get('/api/events')
def events(s:Session=Depends(db),u:User=Depends(current_user)): return [{'event_id':e.event_id,'event':e.event_type,'strategy':e.strategy_id,'symbol':e.symbol,'status':e.status,'duplicate_count':e.duplicate_count,'error':e.error,'received_at':e.received_at.isoformat(),'payload':e.payload} for e in s.query(Event).order_by(Event.id.desc()).limit(300).all()]
@app.get('/api/reports')
def reports(s:Session=Depends(db),u:User=Depends(current_user)):
    rows=s.query(Trade).all(); closed=[t for t in rows if t.exit_time]; gross=sum(t.gross_pnl for t in closed); net=sum(t.net_pnl for t in closed); wins=[t for t in closed if t.net_pnl>0]; losses=[t for t in closed if t.net_pnl<0]; by={}
    for t in closed:
        x=by.setdefault(t.strategy_id,{'trades':0,'net_pnl':0,'wins':0}); x['trades']+=1; x['net_pnl']+=t.net_pnl; x['wins']+=int(t.net_pnl>0)
    return {'closed_trades':len(closed),'gross_pnl':gross,'net_realized_pnl':net,'win_rate':100*len(wins)/max(1,len(closed)),'average_profit':sum(t.net_pnl for t in wins)/max(1,len(wins)),'average_loss':sum(t.net_pnl for t in losses)/max(1,len(losses)),'profit_factor':sum(t.net_pnl for t in wins)/max(1e-9,abs(sum(t.net_pnl for t in losses))),'strategies':by,'trades':[trade_json(t,s) for t in closed]}
@app.get('/api/reports.csv')
def report_csv(s:Session=Depends(db),u:User=Depends(current_user)):
    out=io.StringIO(); w=csv.writer(out); w.writerow(['trade_id','strategy','symbol','direction','quantity','entry_price','exit_price','gross_pnl','charges','net_pnl','status','entry_time','exit_time'])
    for t in s.query(Trade).all():w.writerow([t.trade_id,t.strategy_id,t.symbol,t.direction,t.quantity,t.entry_price,t.exit_price,t.gross_pnl,t.charges,t.net_pnl,t.status,t.entry_time.isoformat(),t.exit_time.isoformat() if t.exit_time else ''])
    return StreamingResponse(iter([out.getvalue()]),media_type='text/csv',headers={'Content-Disposition':'attachment; filename=paper-trades.csv'})
@app.get('/api/settings')
def get_settings(s:Session=Depends(db),u:User=Depends(current_user)): return {'charge_per_unit':setting(s,'charge_per_unit',0),'default_quantity':setting(s,'default_quantity',1),'cookie_secure':os.getenv('COOKIE_SECURE','false').lower()=='true'}
@app.put('/api/settings')
def put_settings(body:dict,s:Session=Depends(db),u:User=Depends(current_user)):
    for k in ('charge_per_unit','default_quantity'):
        if k in body:
            v=float(body[k]);
            if not math.isfinite(v) or (k=='charge_per_unit' and v<0) or (k=='default_quantity' and v<=0): raise HTTPException(422,f'invalid {k}')
            row=s.get(Setting,k) or Setting(key=k,value=str(v)); row.value=str(v); s.add(row)
    s.add(Audit(action='SETTINGS_UPDATE',subject=u.username,details=body)); s.commit(); return {'ok':True}
@app.get('/api/stream')
def stream(u:User=Depends(current_user)):
    async def gen():
        while True:
            yield 'event: refresh\ndata: {}\n\n'; import asyncio; await asyncio.sleep(5)
    return StreamingResponse(gen(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

# The Render image bundles the built React app with the API so its dashboard,
# session cookies, and webhook all share one HTTPS origin.
FRONTEND_DIR=os.path.join(os.path.dirname(__file__),'static')
if os.path.isdir(FRONTEND_DIR):
    assets_dir=os.path.join(FRONTEND_DIR,'assets')
    if os.path.isdir(assets_dir):
        from fastapi.staticfiles import StaticFiles
        app.mount('/assets',StaticFiles(directory=assets_dir),name='assets')
    @app.get('/',include_in_schema=False)
    def frontend_index(): return FileResponse(os.path.join(FRONTEND_DIR,'index.html'))
    @app.get('/{frontend_path:path}',include_in_schema=False)
    def frontend_fallback(frontend_path:str):
        candidate=os.path.abspath(os.path.join(FRONTEND_DIR,frontend_path))
        if candidate.startswith(os.path.abspath(FRONTEND_DIR)+os.sep) and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(FRONTEND_DIR,'index.html'))

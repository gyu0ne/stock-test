import os
import json
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from stock_env import GoogleSheetStockEnv

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# --- Render 환경 변수 로드 ---
# 로컬 테스트시에는 직접 입력하거나 환경변수 설정 필요
GOOGLE_JSON_KEY_STR = os.environ.get("GOOGLE_JSON_KEY")
GOOGLE_SHEET_URL = os.environ.get("GOOGLE_SHEET_URL")

# 환경변수가 없을 경우 (로컬 테스트 등) 에러 방지
if GOOGLE_JSON_KEY_STR:
    json_key = json.loads(GOOGLE_JSON_KEY_STR)
    env = GoogleSheetStockEnv(GOOGLE_SHEET_URL, json_key)
else:
    print("[경고] GOOGLE_JSON_KEY 환경변수가 없습니다. 서버가 정상 작동하지 않을 수 있습니다.")
    env = None

class TradeRequest(BaseModel):
    ticker: str
    qty: int

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    if not env:
        return HTMLResponse(content="<h1>서버 설정 오류: 환경변수를 확인하세요.</h1>", status_code=500)
    
    status = env.get_status()
    return templates.TemplateResponse("index.html", {
        "request": request,
        "status": status
    })

@app.post("/api/buy")
def buy(req: TradeRequest):
    if not env: return {"status": "error", "msg": "서버 설정 오류"}
    return env.buy(req.ticker, req.qty)

@app.post("/api/sell")
def sell(req: TradeRequest):
    if not env: return {"status": "error", "msg": "서버 설정 오류"}
    return env.sell(req.ticker, req.qty)

@app.get("/api/status")
def api_status():
    if not env: return {"status": "error", "msg": "서버 설정 오류"}
    return env.get_status()
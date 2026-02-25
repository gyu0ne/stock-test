import gspread
from oauth2client.service_account import ServiceAccountCredentials
import yfinance as yf
from datetime import datetime
import pytz
import time
from tenacity import retry, stop_after_attempt, wait_exponential

class GoogleSheetStockEnv:
    def __init__(self, sheet_url, json_key_dict, seed_money=10000000):
        self.kst = pytz.timezone('Asia/Seoul')
        self.sheet_url = sheet_url
        self.json_key = json_key_dict
        self.seed_money = seed_money
        
        self._connect_sheet()

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=2, max=10))
    def _connect_sheet(self):
        """구글 시트 연결 (연결 끊기면 재접속 시도)"""
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(self.json_key, scope)
        self.client = gspread.authorize(creds)
        self.sheet = self.client.open_by_url(self.sheet_url).sheet1
        
        # 시트가 비어있으면 헤더 작성
        if not self.sheet.get_all_values():
            self.sheet.append_row(["timestamp", "type", "ticker", "name", "price", "qty", "amount", "balance_after"])
            self.balance = float(self.seed_money)
            self.portfolio = {}
        else:
            self._reconstruct_portfolio()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=60))
    def _safe_append_row(self, row_data):
        """[핵심] 쓰기 제한 걸려도 안전하게 기록"""
        self.sheet.append_row(row_data)

    def _reconstruct_portfolio(self):
        try:
            records = self.sheet.get_all_records()
        except Exception:
            time.sleep(2)
            records = self.sheet.get_all_records()
            
        self.balance = float(self.seed_money)
        self.portfolio = {}
        
        for row in records:
            # 빈 행이거나 잘못된 데이터 패스
            if not row.get('ticker'): continue
            
            ticker = str(row['ticker']).zfill(6)
            qty = int(row['qty'])
            amount = float(row['amount'])
            trade_type = row['type']
            name = row.get('name', ticker)
            
            if ticker not in self.portfolio:
                self.portfolio[ticker] = {'qty': 0, 'total_cost': 0, 'name': name}
            
            if trade_type == 'BUY':
                self.balance -= amount
                self.portfolio[ticker]['qty'] += qty
                self.portfolio[ticker]['total_cost'] += amount
                self.portfolio[ticker]['name'] = name
            elif trade_type == 'SELL':
                self.balance += amount
                self.portfolio[ticker]['qty'] -= qty
                # 매도 시 평단가 총액 차감 로직
                if self.portfolio[ticker]['qty'] > 0:
                    avg = self.portfolio[ticker]['total_cost'] / (self.portfolio[ticker]['qty'] + qty)
                    self.portfolio[ticker]['total_cost'] -= (avg * qty)
                else:
                    self.portfolio[ticker]['total_cost'] = 0

    def get_current_price(self, ticker):
        symbol = f"{ticker}.KS" if ticker.isdigit() else ticker
        try:
            tick = yf.Ticker(symbol)
            return symbol, tick.fast_info['last_price']
        except:
            return symbol, None

    def buy(self, ticker, qty):
        symbol, price = self.get_current_price(ticker)
        if not price: return {"status": "fail", "msg": "가격 조회 실패 (네트워크/일시적 오류)"}
        
        total = price * qty
        if total > self.balance: return {"status": "fail", "msg": "잔액 부족"}
        
        stock_name = ticker 
        # API 호출 아끼기 위해 기존에 있으면 기존 이름 사용
        if ticker in self.portfolio and self.portfolio[ticker]['name']:
            stock_name = self.portfolio[ticker]['name']
        else:
             try: stock_name = yf.Ticker(symbol).info.get('shortName', ticker)
             except: stock_name = ticker

        now = datetime.now(self.kst).strftime("%Y-%m-%d %H:%M:%S")
        self.balance -= total
        
        if ticker not in self.portfolio: 
            self.portfolio[ticker] = {'qty':0, 'total_cost':0, 'name':stock_name}
        
        self.portfolio[ticker]['qty'] += qty
        self.portfolio[ticker]['total_cost'] += total
        self.portfolio[ticker]['name'] = stock_name
        
        self._safe_append_row([now, "BUY", ticker, stock_name, price, qty, total, self.balance])
        return {"status": "success", "msg": f"매수 완료: {stock_name}", "price": price}

    def sell(self, ticker, qty):
        if ticker not in self.portfolio or self.portfolio[ticker]['qty'] < qty:
            return {"status": "fail", "msg": "수량 부족"}
            
        symbol, price = self.get_current_price(ticker)
        if not price: return {"status": "fail", "msg": "가격 조회 실패"}

        total = price * qty
        stock_name = self.portfolio[ticker].get('name', ticker)
        now = datetime.now(self.kst).strftime("%Y-%m-%d %H:%M:%S")
        self.balance += total
        
        self.portfolio[ticker]['qty'] -= qty
        if self.portfolio[ticker]['qty'] > 0:
             avg = self.portfolio[ticker]['total_cost'] / (self.portfolio[ticker]['qty'] + qty)
             self.portfolio[ticker]['total_cost'] -= (avg * qty)
        else:
             self.portfolio[ticker]['total_cost'] = 0
        
        self._safe_append_row([now, "SELL", ticker, stock_name, price, qty, total, self.balance])
        return {"status": "success", "msg": f"매도 완료: {stock_name}", "price": price}

    def get_status(self):
        """대시보드용 전체 상태 반환"""
        total_asset = self.balance
        holdings = []
        
        for ticker, info in self.portfolio.items():
            qty = info['qty']
            if qty > 0:
                # 현재가 조회 (너무 느리면 제외 가능하지만 정확성을 위해 포함)
                _, current_price = self.get_current_price(ticker)
                if not current_price: current_price = 0
                
                val = current_price * qty
                total_asset += val
                
                # 수익률 계산
                cost = info['total_cost']
                profit = val - cost
                roi = (profit / cost * 100) if cost > 0 else 0
                
                holdings.append({
                    "ticker": ticker,
                    "name": info['name'],
                    "qty": qty,
                    "avg_price": round(cost / qty),
                    "current_price": round(current_price),
                    "roi": round(roi, 2),
                    "valuation": round(val)
                })
        
        total_roi = ((total_asset - self.seed_money) / self.seed_money) * 100
        
        return {
            "balance": round(self.balance),
            "total_asset": round(total_asset),
            "total_roi": round(total_roi, 2),
            "holdings": holdings,
            "seed_money": self.seed_money
        }
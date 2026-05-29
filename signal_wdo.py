"""
WDO Signal Generator
Busca dados pré-mercado, analisa via Claude e salva signal.json
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta

BRT = timezone(timedelta(hours=-3))
import yfinance as yf
from anthropic import Anthropic

client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

MONTH_CODES = list("FGHJKMNQUVXZ")

SYMBOLS = {
    "DXY":      ("DX-Y.NYB",  "DXY — Índice do Dólar"),
    "EURUSD":   ("EURUSD=X",  "EUR/USD"),
    "SP500F":   ("ES=F",      "S&P 500 Futures"),
    "NASDAQF":  ("NQ=F",      "Nasdaq Futures"),
    "OURO":     ("GC=F",      "Ouro (XAU/USD)"),
    "PETROLEO": ("CL=F",      "Petróleo WTI"),
    "NIKKEI":   ("^N225",     "Nikkei 225"),
    "DAX":      ("^GDAXI",    "DAX — Alemanha"),
    "USDBRL":   ("USDBRL=X",  "USD/BRL spot"),
}


def auto_contract():
    now = datetime.now(BRT)
    m = now.month - 1
    y = now.year % 100
    if now.day > 10:
        m = (m + 1) % 12
        if m == 0:
            y += 1
    return f"WDO{MONTH_CODES[m]}{y:02d}"


def fetch_market_data():
    data = {}
    for key, (symbol, label) in SYMBOLS.items():
        try:
            hist = yf.Ticker(symbol).history(period="3d", interval="1d")
            if len(hist) >= 2:
                prev  = float(hist["Close"].iloc[-2])
                curr  = float(hist["Close"].iloc[-1])
                chg   = round(((curr - prev) / prev) * 100, 2)
                data[key] = {"label": label, "price": round(curr, 4), "change_pct": chg}
            elif len(hist) == 1:
                curr = float(hist["Close"].iloc[-1])
                data[key] = {"label": label, "price": round(curr, 4), "change_pct": 0.0}
            else:
                data[key] = {"label": label, "price": None, "change_pct": None}
        except Exception as e:
            data[key] = {"label": label, "price": None, "change_pct": None, "erro": str(e)}
    return data


def fetch_usdbrl_history():
    try:
        hist = yf.Ticker("USDBRL=X").history(period="30d", interval="1d")
        return [round(float(v), 4) for v in hist["Close"].tolist()[-20:]]
    except:
        return []


def build_prompt(market_data, history, contract):
    lines = [
        f"Data/Hora análise: {datetime.now(BRT).strftime('%d/%m/%Y %H:%M')} BRT",
        f"Contrato alvo: {contract}",
        "",
        "DADOS PRÉ-MERCADO (antes da abertura da B3 às 9h BRT):",
        "",
    ]
    for key, d in market_data.items():
        if d["price"]:
            sinal = "+" if d["change_pct"] >= 0 else ""
            lines.append(f"• {d['label']}: {d['price']}  ({sinal}{d['change_pct']}%)")
        else:
            lines.append(f"• {d['label']}: indisponível")

    if history:
        lines += ["", f"USD/BRL — fechamentos dos últimos {len(history)} dias: {history}"]

    lines += [
        "",
        "CORRELAÇÕES HISTÓRICAS CONHECIDAS DO WDO:",
        "• DXY subindo  →  WDO tende a subir   (correlação positiva ~0.85)",
        "• EUR/USD subindo  →  WDO tende a cair (correlação negativa)",
        "• S&P Futures caindo + DXY subindo  →  tendência de alta no WDO",
        "• Risk-off global (ouro sobe + bolsas caem)  →  dólar forte  →  WDO pode subir",
        "• Petróleo subindo (BRL se fortalece por pauta exportadora)  →  WDO pode cair",
        "",
        "Com base nesses dados, gere sua análise. Responda SOMENTE com o JSON abaixo, sem texto adicional:",
        "",
        """{
  "bias": "ALTA" | "BAIXA" | "NEUTRO",
  "confianca": 0-100,
  "resumo": "2-3 frases explicando o sinal de forma direta",
  "fator_principal": "o driver mais relevante do sinal hoje",
  "fatores_favor": ["fator 1", "fator 2"],
  "fatores_contra": ["fator 1", "fator 2"],
  "zonas_sugeridas": {
    "resistencia2": número em PONTOS WDO (ex: 5120, não 5.12),
    "resistencia1": número em PONTOS WDO (ex: 5090, não 5.09),
    "suporte1": número em PONTOS WDO (ex: 5040, não 5.04),
    "suporte2": número em PONTOS WDO (ex: 5000, não 5.00)
  },
  "validade": "abertura" | "manha" | "dia_todo",
  "alerta": "aviso se dados conflitantes ou incerteza alta, senão null"
}""",
    ]
    return "\n".join(lines)


def generate_signal():
    contract = auto_contract()
    log = lambda msg: print(f"[{datetime.now(BRT).strftime('%H:%M:%S')}] {msg}", flush=True)

    log(f"Contrato detectado: {contract}")
    log("Buscando dados pré-mercado...")
    market_data = fetch_market_data()

    log("Buscando histórico USD/BRL...")
    history = fetch_usdbrl_history()

    log("Chamando Claude para análise...")
    prompt = build_prompt(market_data, history, contract)

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=700,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        signal = json.loads(raw)
    except json.JSONDecodeError as e:
        log(f"ERRO ao parsear JSON: {e}")
        log(f"Resposta recebida:\n{raw}")
        sys.exit(1)

    signal["contrato"]      = contract
    signal["gerado_em"]     = datetime.now(BRT).isoformat()
    signal["market_data"]   = market_data
    signal["tokens_usados"] = response.usage.input_tokens + response.usage.output_tokens

    with open("signal.json", "w", encoding="utf-8") as f:
        json.dump(signal, f, ensure_ascii=False, indent=2)

    # Acumula histórico de sinais para o backtest usar o sinal real do Claude
    history_file = "signal_history.json"
    try:
        with open(history_file, "r", encoding="utf-8") as f:
            history = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        history = []

    data_hoje = datetime.now(BRT).strftime("%Y-%m-%d")
    history = [h for h in history if h.get("data") != data_hoje]  # remove duplicata do dia
    history.append({
        "data":        data_hoje,
        "bias":        signal["bias"],
        "confianca":   signal["confianca"],
        "contrato":    signal["contrato"],
        "gerado_em":   signal["gerado_em"],
    })
    history = history[-30:]  # mantém apenas os últimos 30 dias

    with open(history_file, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    log(f"SINAL: {signal['bias']} — confiança {signal['confianca']}%")
    log(f"Fator principal: {signal['fator_principal']}")
    log(f"Tokens usados: {signal['tokens_usados']}")
    log("signal.json + signal_history.json salvos com sucesso.")


if __name__ == "__main__":
    generate_signal()

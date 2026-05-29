"""
WDO Backtest — últimos 10 pregões
Analisa abertura 9h–9h30 e cruza com previsão dos indicadores pré-mercado
"""
import json
from datetime import datetime, timezone, timedelta
import yfinance as yf
import pandas as pd

BRT = timezone(timedelta(hours=-3))

# Indicadores e peso no sinal WDO
# Peso positivo = contribui para ALTA WDO quando o indicador sobe
INDICATORS = {
    'DXY':    ('DX-Y.NYB', +1.0),   # DXY sobe → WDO sobe
    'EURUSD': ('EURUSD=X', -1.0),   # EUR/USD sobe → WDO cai
    'SP500':  ('ES=F',     -0.8),   # S&P sobe → risco ligado → WDO cai
    'OURO':   ('GC=F',     +0.4),   # Ouro sobe → risk-off → WDO sobe
}

def fetch_intraday_usdbrl():
    hist = yf.Ticker('USDBRL=X').history(period='45d', interval='30m')
    if hist.empty:
        return None
    hist.index = hist.index.tz_convert('America/Sao_Paulo')
    return hist

def fetch_daily(symbol):
    try:
        df = yf.Ticker(symbol).history(period='60d', interval='1d')
        df.index = df.index.tz_localize(None)
        return df
    except Exception:
        return pd.DataFrame()

def build_signal(prev_changes: dict):
    """Aplica regras mecânicas e retorna (bias, confiança, detalhes)."""
    score = 0.0
    details = {}
    for key, (_, weight) in INDICATORS.items():
        val = prev_changes.get(key)
        if val is not None and not pd.isna(val):
            score += weight * (1.0 if float(val) > 0 else -1.0)
            details[key] = round(float(val), 3)

    if score > 0.6:
        bias = 'ALTA'
    elif score < -0.6:
        bias = 'BAIXA'
    else:
        bias = 'NEUTRO'

    conf = min(int(abs(score) / max(len(details), 1) * 100), 95)
    return bias, conf, details

def run_backtest():
    log = lambda msg: print(f"[BACKTEST] {msg}", flush=True)

    log("Buscando dados intraday USD/BRL (30m)...")
    intraday = fetch_intraday_usdbrl()
    if intraday is None:
        log("ERRO: sem dados intraday"); return

    log("Buscando histórico diário dos indicadores...")
    ind_series = {}
    for key, (symbol, _) in INDICATORS.items():
        df = fetch_daily(symbol)
        if not df.empty:
            s = df['Close'].pct_change() * 100
            s.name = key
            ind_series[key] = s
    ind_df = pd.DataFrame(ind_series) if ind_series else pd.DataFrame()

    # Pregões: dias com barra às 09:00 BRT
    opening = intraday[(intraday.index.hour == 9) & (intraday.index.minute == 0)]
    trading_days = sorted(set(opening.index.date))[-15:]
    log(f"Analisando {len(trading_days)} pregões...")

    sessions = []
    for day in trading_days:
        day_bars = intraday[intraday.index.date == day]
        bars_9h  = day_bars.between_time('09:00', '09:35')
        if bars_9h.empty:
            continue

        # Abertura 9h — primeira barra com hora == 9
        bar_entrada = bars_9h[bars_9h.index.hour == 9].iloc[0]
        open_px     = round(float(bar_entrada['Open']) * 1000 * 2) / 2
        hora_entrada = str(bar_entrada.name.time())[:5]

        # Fechamento 9h30 — primeira barra com minuto >= 30 dentro da janela
        bars_930 = bars_9h[bars_9h.index.minute >= 30]
        if not bars_930.empty:
            bar_saida = bars_930.iloc[0]
            close_px  = round(float(bar_saida['Open']) * 1000 * 2) / 2
            hora_saida = str(bar_saida.name.time())[:5]
        else:
            # fallback: close da última barra da janela (== preço às 9h30)
            bar_saida  = bars_9h.iloc[-1]
            close_px   = round(float(bar_saida['Close']) * 1000 * 2) / 2
            hora_saida = str(bar_saida.name.time())[:5] + ' (close)'

        var_pts = round(close_px - open_px, 1)
        var_pct = round(var_pts / open_px * 100, 3) if open_px else 0
        resultado = 'ALTA' if var_pts > 0 else ('BAIXA' if var_pts < 0 else 'NEUTRO')

        # Variação dos indicadores no dia ANTERIOR ao pregão
        prev_chgs = {}
        if not ind_df.empty:
            before = ind_df[ind_df.index < pd.Timestamp(day)]
            if not before.empty:
                last_row = before.iloc[-1]
                prev_chgs = {k: v for k, v in last_row.items() if not pd.isna(v)}

        sinal, conf, ind_details = build_signal(prev_chgs)
        acertou = (sinal == resultado) if sinal != 'NEUTRO' else None

        sessions.append({
            'data':               str(day),
            'data_fmt':           datetime(day.year, day.month, day.day).strftime('%d/%m'),
            'hora_entrada':       hora_entrada,
            'hora_saida':         hora_saida,
            'abertura_pts':       open_px,
            'fechamento_pts':     close_px,
            'variacao_pts':       var_pts,
            'variacao_pct':       var_pct,
            'resultado':          resultado,
            'sinal_previsto':     sinal,
            'confianca_previsao': conf,
            'acertou':            acertou,
            'indicadores':        ind_details,
        })

    decisive = [s for s in sessions if s['acertou'] is not None]
    acuracia = round(
        len([s for s in decisive if s['acertou']]) / len(decisive) * 100
    ) if decisive else 0

    output = {
        'gerado_em':          datetime.now(BRT).isoformat(),
        'pregoes_analisados': len(sessions),
        'acuracia_pct':       acuracia,
        'acertos':            len([s for s in decisive if s['acertou']]),
        'erros':              len([s for s in decisive if not s['acertou']]),
        'sessions':           sessions,
    }

    with open('backtest.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    log(f"Pregões: {len(sessions)} | Acurácia: {acuracia}% "
        f"({output['acertos']}✓  {output['erros']}✗)")
    log("backtest.json salvo.")

if __name__ == '__main__':
    run_backtest()

/**
 * Main Frontend Application Controller for Groww Algo Trading Platform
 */

const STATE = {
  currentTab: 'terminal',
  activeSymbol: 'NIFTY',
  activeTimeframe: '5m',
  marketData: {
    NIFTY: { ltp: 24500.0, prevClose: 24450.0, high: 24550.0, low: 24420.0 },
    SENSEX: { ltp: 80500.0, prevClose: 80300.0, high: 80650.0, low: 80200.0 },
  },
  candles: [],
  optionChain: null,
  account: null,
  openPositions: [],
  trades: [],
  orders: [],
  strategies: [],
  indicatorsMeta: [],
  ws: null,
  candleChart: null,
  equityChart: null,
};

// ==========================================
// INITIALIZATION
// ==========================================

document.addEventListener('DOMContentLoaded', () => {
  initCharts();
  initNavigation();
  initModals();
  initClock();
  initWebSocket();

  // Initial Data Fetches
  loadSystemSummary();
  loadCandles();
  loadOptionChain();
  loadPaperPortfolio();
  loadStrategies();
  loadRiskAndJournal();
  loadCredentials();

  // Periodic polling for status and MTM
  setInterval(loadSystemSummary, 2000);
  setInterval(loadPaperPortfolio, 3000);
  setInterval(loadOptionChain, 5000);
});

function initCharts() {
  STATE.candleChart = new CandleChart('market-candle-canvas');
  STATE.equityChart = new EquityChart('backtest-equity-canvas');
}

function initNavigation() {
  document.querySelectorAll('.tab-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const target = btn.dataset.tab;
      document.querySelectorAll('.tab-btn').forEach((b) => b.classList.remove('active'));
      document.querySelectorAll('.tab-pane').forEach((p) => p.classList.remove('active'));

      btn.classList.add('active');
      const pane = document.getElementById(`tab-${target}`);
      if (pane) pane.classList.add('active');
      STATE.currentTab = target;

      if (target === 'terminal' && STATE.candleChart) {
        setTimeout(() => STATE.candleChart.render(), 50);
      } else if (target === 'backtest' && STATE.equityChart) {
        setTimeout(() => STATE.equityChart.render(), 50);
      }
    });
  });

  // Timeframe selector buttons
  document.querySelectorAll('.btn-grp-tf').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.btn-grp-tf').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      STATE.activeTimeframe = btn.dataset.tf;
      loadCandles();
    });
  });

  // Symbol selector buttons
  document.querySelectorAll('.btn-grp-symbol').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.btn-grp-symbol').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      STATE.activeSymbol = btn.dataset.symbol;
      loadCandles();
      loadOptionChain();
    });
  });
}

function initClock() {
  const clockEl = document.getElementById('ist-clock');
  function updateTime() {
    const now = new Date();
    // Format in IST
    const options = { timeZone: 'Asia/Kolkata', hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' };
    clockEl.textContent = now.toLocaleTimeString('en-IN', options) + ' IST';
  }
  updateTime();
  setInterval(updateTime, 1000);
}

// ==========================================
// TOAST NOTIFICATIONS
// ==========================================

function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.innerHTML = `
    <span>${message}</span>
    <button style="background:none;border:none;color:#94a3b8;cursor:pointer;" onclick="this.parentElement.remove()">✕</button>
  `;
  container.appendChild(toast);

  setTimeout(() => {
    toast.remove();
  }, 4000);
}

// ==========================================
// WEBSOCKET FEED
// ==========================================

function initWebSocket() {
  const statusPill = document.getElementById('ws-status-pill');
  const statusText = document.getElementById('ws-status-text');

  const loc = window.location;
  const wsProto = loc.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${wsProto}//${loc.host}/api/market/ws`;

  try {
    const ws = new WebSocket(wsUrl);
    STATE.ws = ws;

    ws.onopen = () => {
      statusPill.className = 'status-pill online';
      statusText.textContent = 'CONNECTED';
      // Start ping loop
      STATE.pingTimer = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send('ping');
      }, 25000);
    };

    ws.onmessage = (event) => {
      if (event.data === 'pong') return;
      try {
        const data = JSON.parse(event.data);
        handleSocketMessage(data);
      } catch (err) {
        // ignore raw non-json
      }
    };

    ws.onclose = () => {
      statusPill.className = 'status-pill offline';
      statusText.textContent = 'DISCONNECTED';
      clearInterval(STATE.pingTimer);
      // Reconnect after 3s
      setTimeout(initWebSocket, 3000);
    };

    ws.onerror = () => {
      ws.close();
    };
  } catch (e) {
    statusPill.className = 'status-pill offline';
    statusText.textContent = 'OFFLINE';
  }
}

function handleSocketMessage(data) {
  if (data.type === 'TICK') {
    const sym = data.symbol;
    if (STATE.marketData[sym]) {
      STATE.marketData[sym].ltp = data.ltp;
      updateTickerStrip();
    }
  } else if (data.type === 'CANDLE_UPDATE' || data.type === 'CANDLE_CLOSED') {
    if (data.symbol === STATE.activeSymbol && data.timeframe === STATE.activeTimeframe) {
      updateCandleData(data);
    }
  }
}

function updateCandleData(candleData) {
  if (!STATE.candles || STATE.candles.length === 0) {
    STATE.candles = [candleData];
  } else {
    const last = STATE.candles[STATE.candles.length - 1];
    if (last.timestamp === candleData.timestamp) {
      STATE.candles[STATE.candles.length - 1] = candleData;
    } else {
      STATE.candles.push(candleData);
      if (STATE.candles.length > 120) STATE.candles.shift();
    }
  }
  if (STATE.candleChart) {
    STATE.candleChart.setData(STATE.candles);
  }
}

// ==========================================
// REST API INTEGRATIONS
// ==========================================

async function loadSystemSummary() {
  try {
    const res = await fetch('/api/system/summary');
    if (!res.ok) return;
    const data = await res.json();

    // Mode
    const modeBadge = document.getElementById('execution-mode-badge');
    modeBadge.textContent = data.app.mode;
    modeBadge.className = `mode-badge mode-${data.app.mode.toLowerCase()}`;

    // Circuit Breaker Status
    const breakerBadge = document.getElementById('circuit-breaker-badge');
    const isTripped = data.breaker.is_tripped;
    breakerBadge.textContent = data.breaker.state;
    breakerBadge.className = `breaker-badge ${isTripped ? 'breaker-tripped' : 'breaker-normal'}`;

    const resetBtn = document.getElementById('btn-reset-breaker-nav');
    if (resetBtn) {
      resetBtn.style.display = isTripped ? 'inline-flex' : 'none';
    }

    // Header Quick Stats
    document.getElementById('header-capital-display').textContent = `₹${data.account.equity.toLocaleString('en-IN', { minimumFractionDigits: 2 })}`;

    const pnl = data.account.realized_pnl + data.account.unrealized_pnl;
    const pnlEl = document.getElementById('header-pnl-display');
    pnlEl.textContent = `${pnl >= 0 ? '+' : ''}₹${pnl.toFixed(2)}`;
    pnlEl.className = pnl >= 0 ? 'text-green font-mono' : 'text-red font-mono';

    // Risk Indicators in Risk Tab
    const dailyLossFill = document.getElementById('daily-loss-progress');
    if (dailyLossFill) {
      const lossPct = Math.min(100, (data.account.daily_loss / data.account.max_daily_loss) * 100);
      dailyLossFill.style.width = `${lossPct}%`;
      dailyLossFill.style.backgroundColor = lossPct > 80 ? 'var(--color-red)' : 'var(--color-amber)';
      document.getElementById('daily-loss-text').textContent = `₹${data.account.daily_loss.toFixed(1)} / ₹${data.account.max_daily_loss.toFixed(1)}`;
    }

    const tradeLimitFill = document.getElementById('daily-trades-progress');
    if (tradeLimitFill) {
      const tradesPct = Math.min(100, (data.account.daily_trades / data.account.max_daily_trades) * 100);
      tradeLimitFill.style.width = `${tradesPct}%`;
      tradeLimitFill.style.backgroundColor = tradesPct >= 100 ? 'var(--color-red)' : 'var(--color-blue)';
      document.getElementById('daily-trades-text').textContent = `${data.account.daily_trades} / ${data.account.max_daily_trades} trades`;
    }
  } catch (err) {
    console.error('Failed to load system summary', err);
  }
}

async function loadCandles() {
  try {
    const res = await fetch(`/api/market/candles?symbol=${STATE.activeSymbol}&timeframe=${STATE.activeTimeframe}&limit=80`);
    if (!res.ok) return;
    const candles = await res.json();
    STATE.candles = candles;
    if (STATE.candleChart) {
      STATE.candleChart.setData(candles);
    }
  } catch (err) {
    console.error('Failed to load candles', err);
  }
}

async function loadOptionChain() {
  try {
    const res = await fetch(`/api/market/option-chain?underlying=${STATE.activeSymbol}`);
    if (!res.ok) return;
    const chain = await res.json();
    STATE.optionChain = chain;

    // Update spot price from chain
    if (chain.underlying_ltp && STATE.marketData[STATE.activeSymbol]) {
      STATE.marketData[STATE.activeSymbol].ltp = chain.underlying_ltp;
      updateTickerStrip();
    }

    renderOptionChainTable(chain);
  } catch (err) {
    console.error('Failed to load option chain', err);
  }
}

function updateTickerStrip() {
  for (const sym of ['NIFTY', 'SENSEX']) {
    const data = STATE.marketData[sym];
    const ltpEl = document.getElementById(`ticker-${sym.toLowerCase()}-ltp`);
    const chgEl = document.getElementById(`ticker-${sym.toLowerCase()}-chg`);
    if (!ltpEl || !chgEl) continue;

    ltpEl.textContent = data.ltp.toLocaleString('en-IN', { minimumFractionDigits: 2 });
    const diff = data.ltp - data.prevClose;
    const diffPct = (diff / data.prevClose) * 100;
    const sign = diff >= 0 ? '+' : '';
    chgEl.textContent = `${sign}${diff.toFixed(2)} (${sign}${diffPct.toFixed(2)}%)`;
    chgEl.className = diff >= 0 ? 'ticker-change text-green' : 'ticker-change text-red';
  }
}

function renderOptionChainTable(chain) {
  const tbody = document.getElementById('option-chain-body');
  if (!tbody || !chain || !chain.strikes) return;

  const atmStrike = chain.atm_strike;
  const strikes = chain.strikes.slice(0, 15); // Show 15 nearest strikes

  let html = '';
  strikes.forEach((s) => {
    const isAtm = s.strike_price === atmStrike;
    const ce = s.call_option;
    const pe = s.put_option;

    html += `
      <tr class="${isAtm ? 'atm-row' : ''}">
        <!-- CALLS (CE) -->
        <td class="text-blue">${ce ? ce.greeks.delta.toFixed(2) : '-'}</td>
        <td>${ce ? (ce.greeks.theta).toFixed(1) : '-'}</td>
        <td>${ce ? (ce.implied_volatility * 100).toFixed(1) + '%' : '-'}</td>
        <td class="text-green font-mono" style="font-weight:700;">₹${ce ? ce.ltp.toFixed(2) : '-'}</td>
        <td style="text-align:center;">
          <button class="btn btn-sm btn-success" onclick="quickOrder('${chain.underlying}', ${s.strike_price}, 'CE')">BUY CE</button>
        </td>

        <!-- STRIKE -->
        <td class="strike-col">${s.strike_price}</td>

        <!-- PUTS (PE) -->
        <td style="text-align:center;">
          <button class="btn btn-sm btn-danger" onclick="quickOrder('${chain.underlying}', ${s.strike_price}, 'PE')">BUY PE</button>
        </td>
        <td class="text-red font-mono" style="font-weight:700;">₹${pe ? pe.ltp.toFixed(2) : '-'}</td>
        <td>${pe ? (pe.implied_volatility * 100).toFixed(1) + '%' : '-'}</td>
        <td>${pe ? (pe.greeks.theta).toFixed(1) : '-'}</td>
        <td class="text-blue">${pe ? pe.greeks.delta.toFixed(2) : '-'}</td>
      </tr>
    `;
  });

  tbody.innerHTML = html;
  document.getElementById('chain-atm-label').textContent = `ATM Strike: ${atmStrike}`;
}

async function loadPaperPortfolio() {
  try {
    const [acctRes, posRes, tradesRes, ordersRes] = await Promise.all([
      fetch('/api/paper/account'),
      fetch('/api/paper/positions?open_only=false'),
      fetch('/api/paper/trades?limit=50'),
      fetch('/api/paper/orders?limit=50'),
    ]);

    if (acctRes.ok) {
      STATE.account = await acctRes.json();
      renderAccountSummary(STATE.account);
    }
    if (posRes.ok) {
      STATE.openPositions = await posRes.json();
      renderPositions(STATE.openPositions);
    }
    if (tradesRes.ok) {
      STATE.trades = await tradesRes.json();
      renderTrades(STATE.trades);
    }
    if (ordersRes.ok) {
      STATE.orders = await ordersRes.json();
      renderOrders(STATE.orders);
    }
  } catch (err) {
    console.error('Failed to load paper trading data', err);
  }
}

function renderAccountSummary(acct) {
  const cash = acct.cash !== undefined ? acct.cash : acct.balance || 0;
  const equity = acct.total_equity !== undefined ? acct.total_equity : acct.equity || 0;
  document.getElementById('paper-capital-val').textContent = `₹${(acct.initial_capital || 10000).toLocaleString('en-IN')}`;
  document.getElementById('paper-balance-val').textContent = `₹${cash.toLocaleString('en-IN', { minimumFractionDigits: 2 })}`;
  document.getElementById('paper-equity-val').textContent = `₹${equity.toLocaleString('en-IN', { minimumFractionDigits: 2 })}`;

  const pnlEl = document.getElementById('paper-realized-val');
  pnlEl.textContent = `${acct.realized_pnl >= 0 ? '+' : ''}₹${acct.realized_pnl.toFixed(2)}`;
  pnlEl.className = `stat-value ${acct.realized_pnl >= 0 ? 'text-green' : 'text-red'}`;

  const mtmEl = document.getElementById('paper-unrealized-val');
  mtmEl.textContent = `${acct.unrealized_pnl >= 0 ? '+' : ''}₹${acct.unrealized_pnl.toFixed(2)}`;
  mtmEl.className = `stat-value ${acct.unrealized_pnl >= 0 ? 'text-green' : 'text-red'}`;
}

function renderPositions(positions) {
  const openPos = positions.filter((p) => p.is_open);
  const cardEl = document.getElementById('active-position-card-content');
  const tableBody = document.getElementById('open-positions-table-body');

  // Terminal Widget
  if (openPos.length === 0) {
    cardEl.innerHTML = `
      <div style="text-align:center; padding: 24px 10px; color: var(--text-dim);">
        <p style="font-size: 14px; margin-bottom: 4px;">No Active Positions</p>
        <span style="font-size: 11px;">Algorithm is monitoring for strategy signals.</span>
      </div>
    `;
  } else {
    const pos = openPos[0];
    const isProfit = pos.unrealized_pnl >= 0;
    cardEl.innerHTML = `
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
        <div>
          <span style="font-size: 15px; font-weight: 700; font-family: var(--font-mono); color: #fff;">${pos.contract_symbol}</span>
          <span class="badge ${pos.direction === 'CE' ? 'badge-green' : 'badge-red'}" style="margin-left: 6px;">${pos.direction} BUY</span>
        </div>
        <div style="text-align: right;">
          <div style="font-size: 16px; font-weight: 700; font-family: var(--font-mono);" class="${isProfit ? 'text-green' : 'text-red'}">
            ${isProfit ? '+' : ''}₹${pos.unrealized_pnl.toFixed(2)} (${isProfit ? '+' : ''}${pos.return_pct.toFixed(2)}%)
          </div>
        </div>
      </div>
      <div style="display:grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-bottom: 14px; font-size: 11px;">
        <div class="stat-box" style="padding: 6px 8px;">
          <div class="stat-label">Qty</div>
          <div style="font-weight:700;">${pos.quantity}</div>
        </div>
        <div class="stat-box" style="padding: 6px 8px;">
          <div class="stat-label">Entry</div>
          <div style="font-weight:700;">₹${pos.entry_price.toFixed(2)}</div>
        </div>
        <div class="stat-box" style="padding: 6px 8px;">
          <div class="stat-label">LTP</div>
          <div style="font-weight:700;">₹${pos.current_price.toFixed(2)}</div>
        </div>
        <div class="stat-box" style="padding: 6px 8px;">
          <div class="stat-label">Stop-Loss</div>
          <div style="font-weight:700; color:var(--color-red);">₹${pos.stop_loss_price ? pos.stop_loss_price.toFixed(2) : '-'}</div>
        </div>
      </div>
      <button class="btn btn-danger" style="width:100%;" onclick="closePosition('${pos.id}')">
        Square Off Position
      </button>
    `;
  }

  // Portfolio Tab Table
  if (tableBody) {
    if (openPos.length === 0) {
      tableBody.innerHTML = `<tr><td colspan="9" style="text-align:center; color:var(--text-dim); padding:20px;">No open positions</td></tr>`;
    } else {
      tableBody.innerHTML = openPos
        .map((p) => {
          const isProf = p.unrealized_pnl >= 0;
          return `
            <tr>
              <td class="mono font-bold">${p.contract_symbol}</td>
              <td><span class="badge ${p.direction === 'CE' ? 'badge-green' : 'badge-red'}">${p.direction}</span></td>
              <td class="mono">${p.quantity}</td>
              <td class="mono">₹${p.entry_price.toFixed(2)}</td>
              <td class="mono">₹${p.current_price.toFixed(2)}</td>
              <td class="mono ${isProf ? 'text-green' : 'text-red'}">${isProf ? '+' : ''}₹${p.unrealized_pnl.toFixed(2)} (${p.return_pct.toFixed(2)}%)</td>
              <td class="mono">₹${p.stop_loss_price ? p.stop_loss_price.toFixed(2) : '-'}</td>
              <td class="mono">₹${p.target_price ? p.target_price.toFixed(2) : '-'}</td>
              <td><button class="btn btn-sm btn-danger" onclick="closePosition('${p.id}')">Square Off</button></td>
            </tr>
          `;
        })
        .join('');
    }
  }
}

function renderTrades(trades) {
  const tbody = document.getElementById('trades-table-body');
  if (!tbody) return;

  if (trades.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; color:var(--text-dim); padding:20px;">No trades recorded yet</td></tr>`;
    return;
  }

  tbody.innerHTML = trades
    .map((t) => {
      const isProf = (t.net_pnl || 0) >= 0;
      const timeStr = new Date(t.executed_at).toLocaleTimeString('en-IN', { hour12: false });
      return `
        <tr>
          <td class="mono">${timeStr}</td>
          <td class="mono font-bold">${t.symbol}</td>
          <td><span class="badge ${t.transaction_type === 'BUY' ? 'badge-green' : 'badge-red'}">${t.transaction_type}</span></td>
          <td class="mono">${t.quantity}</td>
          <td class="mono">₹${t.executed_price.toFixed(2)}</td>
          <td class="mono">₹${((t.brokerage || 0) + (t.taxes || 0)).toFixed(2)}</td>
          <td class="mono ${isProf ? 'text-green' : 'text-red'}">${t.net_pnl !== 0 ? (isProf ? '+' : '') + '₹' + t.net_pnl.toFixed(2) : '-'}</td>
        </tr>
      `;
    })
    .join('');
}

function renderOrders(orders) {
  const tbody = document.getElementById('orders-table-body');
  if (!tbody) return;

  if (orders.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; color:var(--text-dim); padding:20px;">No orders found</td></tr>`;
    return;
  }

  tbody.innerHTML = orders
    .map((o) => {
      const timeStr = new Date(o.created_at).toLocaleTimeString('en-IN', { hour12: false });
      let statusBadge = 'badge-blue';
      if (o.status === 'FILLED') statusBadge = 'badge-green';
      if (o.status === 'REJECTED') statusBadge = 'badge-red';

      return `
        <tr>
          <td class="mono">${timeStr}</td>
          <td class="mono">${o.symbol}</td>
          <td>${o.instrument_type}</td>
          <td><span class="badge ${o.transaction_type === 'BUY' ? 'badge-green' : 'badge-red'}">${o.transaction_type}</span></td>
          <td class="mono">${o.quantity}</td>
          <td><span class="badge ${statusBadge}">${o.status}</span></td>
          <td style="color:var(--text-muted); font-size:11px;">${o.rejection_reason || '-'}</td>
        </tr>
      `;
    })
    .join('');
}

// ==========================================
// STRATEGIES & LAB
// ==========================================

async function loadStrategies() {
  try {
    const [stratRes, metaRes] = await Promise.all([
      fetch('/api/strategies'),
      fetch('/api/strategies/meta/indicators'),
    ]);

    if (stratRes.ok) {
      STATE.strategies = await stratRes.json();
      renderStrategyCards(STATE.strategies);
      populateStrategyDropdown(STATE.strategies);
    }
    if (metaRes.ok) {
      STATE.indicatorsMeta = await metaRes.json();
    }
  } catch (err) {
    console.error('Failed to load strategies', err);
  }
}

function renderStrategyCards(strategies) {
  const container = document.getElementById('strategy-cards-container');
  if (!container) return;

  container.innerHTML = strategies
    .map((s) => {
      const cfg = s.config;
      const rt = s.runtime;
      return `
        <div class="card">
          <div class="card-header">
            <span class="card-title" style="color:#fff;">${cfg.name}</span>
            <label class="switch">
              <input type="checkbox" ${rt.is_active ? 'checked' : ''} onchange="toggleStrategy('${cfg.id}', this.checked)">
              <span class="slider"></span>
            </label>
          </div>
          <div class="card-body">
            <div style="font-size: 11px; color: var(--text-muted); margin-bottom: 10px;">${cfg.description || 'Intraday options buying strategy'}</div>
            <div style="display:grid; grid-template-columns: repeat(3, 1fr); gap: 6px; font-size: 11px; margin-bottom: 10px;">
              <div class="stat-box" style="padding: 4px 6px;">
                <div class="stat-label">Instrument</div>
                <div style="font-weight:700;">${cfg.underlying}</div>
              </div>
              <div class="stat-box" style="padding: 4px 6px;">
                <div class="stat-label">Timeframe</div>
                <div style="font-weight:700;">${cfg.timeframe}</div>
              </div>
              <div class="stat-box" style="padding: 4px 6px;">
                <div class="stat-label">Strike</div>
                <div style="font-weight:700;">${cfg.strike_preference}</div>
              </div>
            </div>
            <div style="display:flex; justify-content:space-between; font-size: 11px; color: var(--text-dim);">
              <span>Daily Trades: <strong>${rt.daily_trades}</strong></span>
              <span>Version: <strong>v${cfg.version}</strong></span>
              <span class="${rt.is_active ? 'text-green' : 'text-amber'}">${rt.is_active ? '● LIVE' : '○ PAUSED'}</span>
            </div>
          </div>
        </div>
      `;
    })
    .join('');
}

function populateStrategyDropdown(strategies) {
  const select = document.getElementById('backtest-strategy-select');
  if (!select) return;
  select.innerHTML = strategies
    .map((s) => `<option value="${s.config.id}">${s.config.name} (${s.config.timeframe})</option>`)
    .join('');
}

async function toggleStrategy(strategyId, activate) {
  try {
    const endpoint = activate ? `/api/strategies/${strategyId}/activate` : `/api/strategies/${strategyId}/deactivate`;
    const res = await fetch(endpoint, { method: 'POST' });
    if (!res.ok) throw new Error('Failed to toggle strategy');
    showToast(`Strategy ${activate ? 'activated' : 'deactivated'} successfully`, 'success');
    loadStrategies();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

// ==========================================
// BACKTESTING ENGINE
// ==========================================

async function runBacktest() {
  const btn = document.getElementById('btn-run-backtest');
  btn.disabled = true;
  btn.textContent = 'Running Backtest...';

  try {
    const payload = {
      strategy_id: document.getElementById('backtest-strategy-select').value,
      symbol: document.getElementById('backtest-symbol-select').value,
      timeframe: document.getElementById('backtest-tf-select').value,
      from_date: new Date(Date.now() - 10 * 86400000).toISOString(),
      to_date: new Date().toISOString(),
      initial_capital: parseFloat(document.getElementById('backtest-capital-input').value) || 10000.0,
      strike_preference: document.getElementById('backtest-strike-select').value,
      slippage_pct: parseFloat(document.getElementById('backtest-slippage-input').value) || 0.5,
    };

    const res = await fetch('/api/backtest/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Backtest failed');
    }

    const result = await res.json();
    renderBacktestResults(result);
    showToast('Backtest completed successfully!', 'success');
  } catch (err) {
    showToast(`Error: ${err.message}`, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Run Backtest';
  }
}

async function seedBacktestCandles() {
  try {
    const symbol = document.getElementById('backtest-symbol-select').value;
    const timeframe = document.getElementById('backtest-tf-select').value;
    const res = await fetch('/api/backtest/seed-candles', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol, timeframe, days: 15 }),
    });
    if (!res.ok) throw new Error('Failed to seed candles');
    const data = await res.json();
    showToast(`Seeded ${data.count} synthetic candles for ${symbol}`, 'success');
  } catch (err) {
    showToast(err.message, 'error');
  }
}

function renderBacktestResults(result) {
  const s = result.summary;
  document.getElementById('bt-net-pnl').textContent = `${s.net_pnl >= 0 ? '+' : ''}₹${s.net_pnl.toFixed(2)} (${s.return_pct.toFixed(2)}%)`;
  document.getElementById('bt-net-pnl').className = `stat-value ${s.net_pnl >= 0 ? 'text-green' : 'text-red'}`;

  document.getElementById('bt-win-rate').textContent = `${s.win_rate_pct.toFixed(1)}%`;
  document.getElementById('bt-trades-count').textContent = s.total_trades;
  document.getElementById('bt-profit-factor').textContent = s.profit_factor.toFixed(2);
  document.getElementById('bt-max-dd').textContent = `${s.max_drawdown_pct.toFixed(2)}%`;
  document.getElementById('bt-sharpe').textContent = s.sharpe_ratio.toFixed(2);
  document.getElementById('bt-sortino').textContent = s.sortino_ratio.toFixed(2);

  if (STATE.equityChart && result.equity_curve) {
    STATE.equityChart.setData(result.equity_curve);
  }

  // Render backtest trades
  const tbody = document.getElementById('backtest-trades-body');
  if (tbody && result.trades) {
    tbody.innerHTML = result.trades
      .map((t) => {
        const isProf = t.net_pnl >= 0;
        return `
          <tr>
            <td class="mono">${new Date(t.entry_time).toLocaleDateString()}</td>
            <td class="mono font-bold">${t.strike_price} ${t.option_type}</td>
            <td class="mono">₹${t.entry_price.toFixed(2)}</td>
            <td class="mono">₹${t.exit_price.toFixed(2)}</td>
            <td class="mono">₹${t.charges.toFixed(2)}</td>
            <td class="mono ${isProf ? 'text-green' : 'text-red'}">${isProf ? '+' : ''}₹${t.net_pnl.toFixed(2)}</td>
            <td><span class="badge ${isProf ? 'badge-green' : 'badge-red'}">${t.exit_reason}</span></td>
          </tr>
        `;
      })
      .join('');
  }
}

// ==========================================
// RISK & TRADE JOURNAL
// ==========================================

async function loadRiskAndJournal() {
  try {
    const [scoreRes, entriesRes, eventsRes] = await Promise.all([
      fetch('/api/journal/scorecard'),
      fetch('/api/journal/entries?limit=30'),
      fetch('/api/risk/events?limit=30'),
    ]);

    if (scoreRes.ok) {
      const score = await scoreRes.json();
      renderDisciplineScorecard(score);
    }
    if (entriesRes.ok) {
      const entries = await entriesRes.json();
      renderJournalEntries(entries);
    }
    if (eventsRes.ok) {
      const events = await eventsRes.json();
      renderRiskEvents(events);
    }
  } catch (err) {
    console.error('Failed to load risk/journal data', err);
  }
}

function renderDisciplineScorecard(score) {
  const badge = document.getElementById('scorecard-grade-badge');
  if (badge) {
    badge.textContent = score.grade;
    badge.className = `grade-badge grade-${score.grade[0]}`;
  }
  document.getElementById('scorecard-score-pct').textContent = `${score.score_pct.toFixed(1)}% Discipline Score`;
  document.getElementById('scorecard-rules-followed').textContent = score.rules_followed ? 'YES' : 'NO';
  document.getElementById('scorecard-mistakes-count').textContent = score.mistakes_count;
}

function renderJournalEntries(entries) {
  const tbody = document.getElementById('journal-entries-body');
  if (!tbody) return;

  if (entries.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color:var(--text-dim); padding:20px;">No journal entries yet</td></tr>`;
    return;
  }

  tbody.innerHTML = entries
    .map((e) => {
      const isProf = e.net_pnl >= 0;
      const tagsHtml = (e.tags || [])
        .map((t) => `<span class="badge badge-amber" style="margin-right:4px;">${t}</span>`)
        .join('');

      return `
        <tr>
          <td class="mono font-bold">${e.contract_symbol}</td>
          <td class="mono">${e.exit_reason}</td>
          <td class="mono ${isProf ? 'text-green' : 'text-red'}">${isProf ? '+' : ''}₹${e.net_pnl.toFixed(2)}</td>
          <td>${tagsHtml || '<span style="color:var(--text-dim);">-</span>'}</td>
          <td style="color:var(--text-muted);">${e.notes || '-'}</td>
          <td>
            <button class="btn btn-sm btn-secondary" onclick="openJournalModal('${e.id}', '${(e.tags || []).join(',')}', '${escape(e.notes || '')}')">Edit Note</button>
          </td>
        </tr>
      `;
    })
    .join('');
}

function renderRiskEvents(events) {
  const tbody = document.getElementById('risk-events-body');
  if (!tbody) return;

  if (events.length === 0) {
    tbody.innerHTML = `<tr><td colspan="4" style="text-align:center; color:var(--text-dim); padding:20px;">No risk violations recorded</td></tr>`;
    return;
  }

  tbody.innerHTML = events
    .map((ev) => `
      <tr>
        <td class="mono">${new Date(ev.created_at).toLocaleTimeString()}</td>
        <td><span class="badge badge-red">${ev.event_type}</span></td>
        <td>${ev.reason}</td>
        <td><span class="badge badge-blue">${ev.mode}</span></td>
      </tr>
    `)
    .join('');
}

// ==========================================
// ACTIONS & CONTROLS
// ==========================================

async function triggerKillSwitch() {
  const reason = document.getElementById('kill-switch-reason').value || 'Manual emergency trigger from dashboard';
  const liquidate = document.getElementById('kill-switch-liquidate').checked;

  try {
    const res = await fetch('/api/risk/kill-switch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason, liquidate_open_positions: liquidate }),
    });
    if (!res.ok) throw new Error('Failed to activate kill switch');
    showToast('Kill-switch engaged! Trading halted.', 'warning');
    closeModal('modal-kill-switch');
    loadSystemSummary();
    loadPaperPortfolio();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

async function resetCircuitBreaker() {
  try {
    const res = await fetch('/api/risk/reset-breaker', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason: 'Manual operator reset', confirmed: true }),
    });
    if (!res.ok) throw new Error('Failed to reset circuit breaker');
    showToast('Circuit breaker reset to NORMAL', 'success');
    loadSystemSummary();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

async function resetPaperAccount() {
  if (!confirm('Are you sure you want to reset your paper account balance to ₹10,000?')) return;
  try {
    const res = await fetch('/api/paper/reset', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ initial_capital: 10000.0 }),
    });
    if (!res.ok) throw new Error('Failed to reset account');
    showToast('Paper account balance reset to ₹10,000', 'success');
    loadPaperPortfolio();
    loadSystemSummary();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

async function closePosition(positionId) {
  try {
    const res = await fetch(`/api/paper/positions/${positionId}/close`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason: 'MANUAL' }),
    });
    if (!res.ok) throw new Error('Failed to square off position');
    showToast('Position squared off successfully', 'success');
    loadPaperPortfolio();
    loadSystemSummary();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

function quickOrder(symbol, strike, direction) {
  document.getElementById('order-symbol').value = symbol;
  document.getElementById('order-strike').value = strike;
  document.getElementById('order-direction').value = direction;
  openModal('modal-quick-order');
}

async function submitManualOrder() {
  try {
    const symbol = document.getElementById('order-symbol').value;
    const direction = document.getElementById('order-direction').value;
    const strike = parseFloat(document.getElementById('order-strike').value);
    const quantity = parseInt(document.getElementById('order-qty').value, 10);

    const payload = {
      symbol: symbol,
      instrument_type: direction,
      strike_preference: 'ATM',
      transaction_type: 'BUY',
      quantity: quantity,
      order_type: 'MARKET',
    };

    const res = await fetch('/api/paper/orders', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    const data = await res.json();
    if (!res.ok || !data.is_filled) {
      throw new Error(data.message || 'Order rejected by risk management');
    }

    showToast(`Order filled: ${direction} BUY ${quantity} qty`, 'success');
    closeModal('modal-quick-order');
    loadPaperPortfolio();
    loadSystemSummary();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

// ==========================================
// CREDENTIALS & SETTINGS
// ==========================================

async function loadCredentials() {
  try {
    const res = await fetch('/api/system/credentials');
    if (!res.ok) return;
    const data = await res.json();

    const statusBadge = document.getElementById('broker-status-badge');
    if (statusBadge) {
      statusBadge.textContent = data.is_configured ? 'CONFIGURED' : 'NOT CONFIGURED';
      statusBadge.className = `badge ${data.is_configured ? 'badge-green' : 'badge-amber'}`;
    }

    if (data.api_key_masked) document.getElementById('cfg-api-key').placeholder = data.api_key_masked;
    if (data.api_secret_masked) document.getElementById('cfg-api-secret').placeholder = data.api_secret_masked;
    if (data.totp_key_masked) document.getElementById('cfg-totp-key').placeholder = data.totp_key_masked;
  } catch (err) {
    console.error('Failed to load credentials', err);
  }
}

async function saveCredentials() {
  try {
    const apiKey = document.getElementById('cfg-api-key').value.trim() || undefined;
    const apiSecret = document.getElementById('cfg-api-secret').value.trim() || undefined;
    const totpKey = document.getElementById('cfg-totp-key').value.trim() || undefined;

    const res = await fetch('/api/system/credentials', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ api_key: apiKey, api_secret: apiSecret, totp_key: totpKey }),
    });

    if (!res.ok) throw new Error('Failed to update credentials');
    showToast('Groww credentials saved securely', 'success');
    loadCredentials();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

// ==========================================
// MODALS
// ==========================================

function initModals() {
  document.querySelectorAll('.modal-close').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      const modal = e.target.closest('.modal-overlay');
      if (modal) modal.classList.remove('active');
    });
  });
}

function openModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.add('active');
}

function closeModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.remove('active');
}

let activeJournalId = null;
function openJournalModal(id, tagsStr, notes) {
  activeJournalId = id;
  document.getElementById('journal-tags-input').value = tagsStr;
  document.getElementById('journal-notes-input').value = unescape(notes);
  openModal('modal-journal');
}

async function saveJournalNote() {
  if (!activeJournalId) return;
  try {
    const tags = document
      .getElementById('journal-tags-input')
      .value.split(',')
      .map((s) => s.trim().toUpperCase())
      .filter((s) => s.length > 0);
    const notes = document.getElementById('journal-notes-input').value;

    const res = await fetch('/api/journal/entries', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: activeJournalId, tags, notes }),
    });

    if (!res.ok) throw new Error('Failed to save journal note');
    showToast('Trade reflection saved', 'success');
    closeModal('modal-journal');
    loadRiskAndJournal();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

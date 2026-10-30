/**
 * Custom Canvas Chart Engine for Groww Algo Trading Platform
 * Supports high-DPI Candlestick charts with EMA overlay & volume bars,
 * and Backtest Equity curves with gradient fills and crosshairs.
 */

class CandleChart {
  constructor(canvasId) {
    this.canvas = document.getElementById(canvasId);
    if (!this.canvas) return;
    this.ctx = this.canvas.getContext('2d');
    this.candles = [];
    this.hoverIdx = -1;
    this.mouseX = 0;
    this.mouseY = 0;
    this.padding = { top: 20, right: 65, bottom: 45, left: 10 };
    this.volumeHeightPct = 0.22; // 22% for volume sub-chart

    this._setupCanvas();
    this._bindEvents();
  }

  _setupCanvas() {
    if (!this.canvas) return;
    const rect = this.canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    this.width = rect.width;
    this.height = rect.height;
    this.canvas.width = Math.floor(rect.width * dpr);
    this.canvas.height = Math.floor(rect.height * dpr);
    this.ctx.scale(dpr, dpr);
  }

  _bindEvents() {
    if (!this.canvas) return;
    window.addEventListener('resize', () => {
      this._setupCanvas();
      this.render();
    });

    this.canvas.addEventListener('mousemove', (e) => {
      const rect = this.canvas.getBoundingClientRect();
      this.mouseX = e.clientX - rect.left;
      this.mouseY = e.clientY - rect.top;
      this._updateHover();
      this.render();
    });

    this.canvas.addEventListener('mouseleave', () => {
      this.hoverIdx = -1;
      this.render();
    });
  }

  setData(candles) {
    this.candles = candles || [];
    this._setupCanvas();
    this.render();
  }

  _calculateEMA(candles, period = 20) {
    if (candles.length < period) return [];
    const k = 2 / (period + 1);
    const emaValues = new Array(candles.length).fill(null);
    let sum = 0;
    for (let i = 0; i < period; i++) sum += candles[i].close;
    let prevEMA = sum / period;
    emaValues[period - 1] = prevEMA;

    for (let i = period; i < candles.length; i++) {
      const currentEMA = candles[i].close * k + prevEMA * (1 - k);
      emaValues[i] = currentEMA;
      prevEMA = currentEMA;
    }
    return emaValues;
  }

  _updateHover() {
    if (!this.candles.length) {
      this.hoverIdx = -1;
      return;
    }
    const chartW = this.width - this.padding.left - this.padding.right;
    const step = chartW / this.candles.length;
    const idx = Math.floor((this.mouseX - this.padding.left) / step);
    if (idx >= 0 && idx < this.candles.length) {
      this.hoverIdx = idx;
    } else {
      this.hoverIdx = -1;
    }
  }

  render() {
    if (!this.ctx || !this.width || !this.height) return;
    const { ctx, width, height, padding, candles } = this;

    ctx.clearRect(0, 0, width, height);

    if (!candles || candles.length === 0) {
      ctx.fillStyle = '#64748b';
      ctx.font = '12px ui-monospace, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('No candle data available', width / 2, height / 2);
      return;
    }

    const chartW = width - padding.left - padding.right;
    const totalChartH = height - padding.top - padding.bottom;
    const volH = totalChartH * this.volumeHeightPct;
    const priceH = totalChartH - volH - 10;

    // Price Bounds
    let minPrice = Infinity;
    let maxPrice = -Infinity;
    let maxVol = 0;

    for (const c of candles) {
      if (c.low < minPrice) minPrice = c.low;
      if (c.high > maxPrice) maxPrice = c.high;
      if (c.volume > maxVol) maxVol = c.volume;
    }
    if (minPrice === maxPrice) {
      minPrice -= 5;
      maxPrice += 5;
    }
    const priceRange = maxPrice - minPrice;
    const pricePad = priceRange * 0.05;
    const effectiveMin = minPrice - pricePad;
    const effectiveMax = maxPrice + pricePad;
    const effectiveRange = effectiveMax - effectiveMin;

    const getY = (val) => padding.top + priceH - ((val - effectiveMin) / effectiveRange) * priceH;
    const getVolY = (vol) => height - padding.bottom - (maxVol ? (vol / maxVol) * volH : 0);
    const step = chartW / candles.length;
    const candleWidth = Math.max(2, Math.min(step * 0.7, 14));

    // Draw Price Grid & Axes
    ctx.strokeStyle = '#1e293b';
    ctx.lineWidth = 1;
    ctx.fillStyle = '#64748b';
    ctx.font = '10px ui-monospace, monospace';
    ctx.textAlign = 'left';

    const numGrids = 5;
    for (let i = 0; i <= numGrids; i++) {
      const p = effectiveMin + (effectiveRange * i) / numGrids;
      const y = getY(p);
      ctx.beginPath();
      ctx.moveTo(padding.left, y);
      ctx.lineTo(width - padding.right, y);
      ctx.stroke();
      ctx.fillText(p.toFixed(1), width - padding.right + 6, y + 3);
    }

    // Volume Separator line
    const volSepY = height - padding.bottom - volH - 4;
    ctx.beginPath();
    ctx.moveTo(padding.left, volSepY);
    ctx.lineTo(width - padding.right, volSepY);
    ctx.stroke();

    // Draw Volume Bars
    candles.forEach((c, i) => {
      const x = padding.left + i * step + step / 2;
      const vy = getVolY(c.volume);
      const vh = height - padding.bottom - vy;
      const isBull = c.close >= c.open;
      ctx.fillStyle = isBull ? 'rgba(16, 185, 129, 0.25)' : 'rgba(244, 63, 94, 0.25)';
      ctx.fillRect(x - candleWidth / 2, vy, candleWidth, vh);
    });

    // Draw Candlesticks
    candles.forEach((c, i) => {
      const x = padding.left + i * step + step / 2;
      const isBull = c.close >= c.open;
      const color = isBull ? '#10b981' : '#f43f5e';

      const highY = getY(c.high);
      const lowY = getY(c.low);
      const openY = getY(c.open);
      const closeY = getY(c.close);

      // Wick
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.moveTo(x, highY);
      ctx.lineTo(x, lowY);
      ctx.stroke();

      // Body
      const bodyTop = Math.min(openY, closeY);
      const bodyH = Math.max(Math.abs(closeY - openY), 1.5);
      ctx.fillStyle = color;
      ctx.fillRect(x - candleWidth / 2, bodyTop, candleWidth, bodyH);
    });

    // 20-period EMA Overlay
    const emaValues = this._calculateEMA(candles, 20);
    ctx.strokeStyle = '#38bdf8';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    let started = false;
    candles.forEach((_, i) => {
      if (emaValues[i] !== null) {
        const x = padding.left + i * step + step / 2;
        const y = getY(emaValues[i]);
        if (!started) {
          ctx.moveTo(x, y);
          started = true;
        } else {
          ctx.lineTo(x, y);
        }
      }
    });
    ctx.stroke();

    // Time Axis Labels (Sampling every ~15-20 candles)
    ctx.fillStyle = '#64748b';
    ctx.textAlign = 'center';
    const labelInterval = Math.max(1, Math.floor(candles.length / 5));
    candles.forEach((c, i) => {
      if (i % labelInterval === 0 || i === candles.length - 1) {
        const x = padding.left + i * step + step / 2;
        const dt = new Date(c.timestamp);
        const timeStr = dt.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false });
        ctx.fillText(timeStr, x, height - padding.bottom + 16);
      }
    });

    // Crosshair & Tooltip
    if (this.hoverIdx >= 0 && this.hoverIdx < candles.length) {
      const hc = candles[this.hoverIdx];
      const hx = padding.left + this.hoverIdx * step + step / 2;
      const hy = getY(hc.close);

      // Crosshair lines
      ctx.strokeStyle = 'rgba(148, 163, 184, 0.4)';
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 3]);

      // Vertical
      ctx.beginPath();
      ctx.moveTo(hx, padding.top);
      ctx.lineTo(hx, height - padding.bottom);
      ctx.stroke();

      // Horizontal
      ctx.beginPath();
      ctx.moveTo(padding.left, hy);
      ctx.lineTo(width - padding.right, hy);
      ctx.stroke();
      ctx.setLineDash([]);

      // Current hovered price pill on Y axis
      ctx.fillStyle = '#1e293b';
      ctx.fillRect(width - padding.right + 2, hy - 9, 58, 18);
      ctx.fillStyle = '#f8fafc';
      ctx.textAlign = 'left';
      ctx.fillText(hc.close.toFixed(1), width - padding.right + 6, hy + 4);

      // Tooltip Card
      this._drawTooltip(hc, hx, padding.top + 10);
    }
  }

  _drawTooltip(c, x, y) {
    const { ctx } = this;
    const isBull = c.close >= c.open;
    const diff = c.close - c.open;
    const diffPct = (diff / c.open) * 100;
    const dateStr = new Date(c.timestamp).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });

    const lines = [
      `${dateStr}  ${isBull ? '▲' : '▼'} ${diff >= 0 ? '+' : ''}${diff.toFixed(1)} (${diffPct.toFixed(2)}%)`,
      `O: ${c.open.toFixed(1)}  H: ${c.high.toFixed(1)}  L: ${c.low.toFixed(1)}  C: ${c.close.toFixed(1)}`,
      `Vol: ${c.volume.toLocaleString()}`,
    ];

    const boxW = 220;
    const boxH = 54;
    let boxX = x + 12;
    if (boxX + boxW > this.width - this.padding.right) {
      boxX = x - boxW - 12;
    }

    ctx.fillStyle = 'rgba(15, 23, 42, 0.9)';
    ctx.strokeStyle = '#334155';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.roundRect(boxX, y, boxW, boxH, 4);
    ctx.fill();
    ctx.stroke();

    ctx.font = '10px ui-monospace, monospace';
    ctx.textAlign = 'left';
    ctx.fillStyle = isBull ? '#10b981' : '#f43f5e';
    ctx.fillText(lines[0], boxX + 8, y + 16);

    ctx.fillStyle = '#e2e8f0';
    ctx.fillText(lines[1], boxX + 8, y + 32);

    ctx.fillStyle = '#94a3b8';
    ctx.fillText(lines[2], boxX + 8, y + 46);
  }
}


class EquityChart {
  constructor(canvasId) {
    this.canvas = document.getElementById(canvasId);
    if (!this.canvas) return;
    this.ctx = this.canvas.getContext('2d');
    this.points = [];
    this.hoverIdx = -1;
    this.mouseX = 0;
    this.mouseY = 0;
    this.padding = { top: 20, right: 70, bottom: 35, left: 15 };

    this._setupCanvas();
    this._bindEvents();
  }

  _setupCanvas() {
    if (!this.canvas) return;
    const rect = this.canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    this.width = rect.width;
    this.height = rect.height;
    this.canvas.width = Math.floor(rect.width * dpr);
    this.canvas.height = Math.floor(rect.height * dpr);
    this.ctx.scale(dpr, dpr);
  }

  _bindEvents() {
    if (!this.canvas) return;
    window.addEventListener('resize', () => {
      this._setupCanvas();
      this.render();
    });

    this.canvas.addEventListener('mousemove', (e) => {
      const rect = this.canvas.getBoundingClientRect();
      this.mouseX = e.clientX - rect.left;
      this.mouseY = e.clientY - rect.top;
      this._updateHover();
      this.render();
    });

    this.canvas.addEventListener('mouseleave', () => {
      this.hoverIdx = -1;
      this.render();
    });
  }

  setData(points) {
    this.points = points || [];
    this._setupCanvas();
    this.render();
  }

  _updateHover() {
    if (!this.points.length) {
      this.hoverIdx = -1;
      return;
    }
    const chartW = this.width - this.padding.left - this.padding.right;
    const step = chartW / Math.max(1, this.points.length - 1);
    const idx = Math.round((this.mouseX - this.padding.left) / step);
    if (idx >= 0 && idx < this.points.length) {
      this.hoverIdx = idx;
    } else {
      this.hoverIdx = -1;
    }
  }

  render() {
    if (!this.ctx || !this.width || !this.height) return;
    const { ctx, width, height, padding, points } = this;

    ctx.clearRect(0, 0, width, height);

    if (!points || points.length === 0) {
      ctx.fillStyle = '#64748b';
      ctx.font = '12px ui-monospace, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('Run a backtest to display equity curve', width / 2, height / 2);
      return;
    }

    const chartW = width - padding.left - padding.right;
    const chartH = height - padding.top - padding.bottom;

    let minEq = Infinity;
    let maxEq = -Infinity;
    for (const p of points) {
      if (p.equity < minEq) minEq = p.equity;
      if (p.equity > maxEq) maxEq = p.equity;
    }
    if (minEq === maxEq) {
      minEq -= 500;
      maxEq += 500;
    }
    const range = maxEq - minEq;
    const pad = range * 0.1;
    const effectiveMin = minEq - pad;
    const effectiveMax = maxEq + pad;
    const effectiveRange = effectiveMax - effectiveMin;

    const getY = (val) => padding.top + chartH - ((val - effectiveMin) / effectiveRange) * chartH;
    const step = chartW / Math.max(1, points.length - 1);

    // Draw Grid & Labels
    ctx.strokeStyle = '#1e293b';
    ctx.lineWidth = 1;
    ctx.fillStyle = '#64748b';
    ctx.font = '10px ui-monospace, monospace';
    ctx.textAlign = 'left';

    const numGrids = 4;
    for (let i = 0; i <= numGrids; i++) {
      const eq = effectiveMin + (effectiveRange * i) / numGrids;
      const y = getY(eq);
      ctx.beginPath();
      ctx.moveTo(padding.left, y);
      ctx.lineTo(width - padding.right, y);
      ctx.stroke();
      ctx.fillText('₹' + eq.toFixed(0), width - padding.right + 6, y + 3);
    }

    // Baseline Line (First point equity)
    const baseEq = points[0].equity;
    const baseY = getY(baseEq);
    ctx.strokeStyle = '#334155';
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(padding.left, baseY);
    ctx.lineTo(width - padding.right, baseY);
    ctx.stroke();
    ctx.setLineDash([]);

    // Gradient fill under curve
    const gradient = ctx.createLinearGradient(0, padding.top, 0, height - padding.bottom);
    gradient.addColorStop(0, 'rgba(16, 185, 129, 0.35)');
    gradient.addColorStop(1, 'rgba(16, 185, 129, 0.0)');

    ctx.fillStyle = gradient;
    ctx.beginPath();
    ctx.moveTo(padding.left, height - padding.bottom);
    points.forEach((p, i) => {
      const x = padding.left + i * step;
      const y = getY(p.equity);
      ctx.lineTo(x, y);
    });
    ctx.lineTo(padding.left + (points.length - 1) * step, height - padding.bottom);
    ctx.closePath();
    ctx.fill();

    // Line Path
    ctx.strokeStyle = '#10b981';
    ctx.lineWidth = 2;
    ctx.beginPath();
    points.forEach((p, i) => {
      const x = padding.left + i * step;
      const y = getY(p.equity);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();

    // Time Labels
    ctx.fillStyle = '#64748b';
    ctx.textAlign = 'center';
    const labelInterval = Math.max(1, Math.floor(points.length / 5));
    points.forEach((p, i) => {
      if (i % labelInterval === 0 || i === points.length - 1) {
        const x = padding.left + i * step;
        const dt = new Date(p.timestamp);
        const str = dt.toLocaleDateString('en-IN', { month: 'short', day: 'numeric' });
        ctx.fillText(str, x, height - padding.bottom + 16);
      }
    });

    // Hover Crosshair & Tooltip
    if (this.hoverIdx >= 0 && this.hoverIdx < points.length) {
      const hp = points[this.hoverIdx];
      const hx = padding.left + this.hoverIdx * step;
      const hy = getY(hp.equity);

      ctx.strokeStyle = 'rgba(148, 163, 184, 0.5)';
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(hx, padding.top);
      ctx.lineTo(hx, height - padding.bottom);
      ctx.stroke();
      ctx.setLineDash([]);

      // Point circle
      ctx.fillStyle = '#10b981';
      ctx.beginPath();
      ctx.arc(hx, hy, 4, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = '#ffffff';
      ctx.stroke();

      // Tooltip
      const boxW = 160;
      const boxH = 46;
      let boxX = hx + 10;
      if (boxX + boxW > width - padding.right) boxX = hx - boxW - 10;
      const boxY = Math.max(padding.top + 5, hy - boxH - 5);

      ctx.fillStyle = 'rgba(15, 23, 42, 0.9)';
      ctx.strokeStyle = '#334155';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.roundRect(boxX, boxY, boxW, boxH, 4);
      ctx.fill();
      ctx.stroke();

      ctx.font = '10px ui-monospace, monospace';
      ctx.textAlign = 'left';
      ctx.fillStyle = '#f8fafc';
      ctx.fillText(`Equity: ₹${hp.equity.toFixed(2)}`, boxX + 8, boxY + 16);

      const pnl = hp.equity - baseEq;
      ctx.fillStyle = pnl >= 0 ? '#10b981' : '#f43f5e';
      ctx.fillText(`P&L: ${pnl >= 0 ? '+' : ''}₹${pnl.toFixed(2)} (${hp.drawdown_pct.toFixed(2)}% DD)`, boxX + 8, boxY + 32);
    }
  }
}

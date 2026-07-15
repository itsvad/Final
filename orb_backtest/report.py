"""
Generates a self-contained HTML report (results/report.html) summarizing a
backtest run: stat tiles, an equity curve, and the full trade log.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

import pandas as pd

from engine import ORBConfig


def _fmt_money(x: float) -> str:
    sign = "-" if x < 0 else ""
    return f"{sign}${abs(x):,.2f}"


def _fmt_pct(x: float) -> str:
    sign = "+" if x > 0 else ""
    return f"{sign}{x:.2f}%"


def _classify(pnl: float) -> str:
    if pnl > 0:
        return "win"
    if pnl < 0:
        return "loss"
    return "be"


def _stat_tile(label: str, value: str, tone: str = "") -> str:
    tone_class = f" tone-{tone}" if tone else ""
    return f"""
    <div class="tile">
      <div class="tile-label">{html.escape(label)}</div>
      <div class="tile-value{tone_class}">{value}</div>
    </div>"""


def generate_html_report(
    trades_df: pd.DataFrame,
    stats: dict,
    cfg: ORBConfig,
    meta: dict,
    out_path: Path,
) -> None:
    df = trades_df.copy().reset_index(drop=True)
    df["equity_after"] = cfg.account_size + df["pnl"].cumsum()
    df["outcome"] = df["pnl"].apply(_classify)

    # ---- equity curve series (index 0 = starting balance) ----
    curve_labels = ["Start"] + [f"#{i+1}" for i in range(len(df))]
    curve_values = [cfg.account_size] + df["equity_after"].tolist()
    curve_dates = [meta.get("start", "")] + df["date"].astype(str).tolist()
    curve_pnls = [0.0] + df["pnl"].tolist()

    v_min, v_max = min(curve_values), max(curve_values)
    pad = max((v_max - v_min) * 0.12, 1.0)
    v_min -= pad
    v_max += pad

    plot_w, plot_h = 860, 240
    margin_l, margin_r, margin_t, margin_b = 54, 20, 16, 28
    inner_w = plot_w - margin_l - margin_r
    inner_h = plot_h - margin_t - margin_b
    n = len(curve_values)

    def x_at(i):
        return margin_l + (inner_w * i / (n - 1) if n > 1 else inner_w / 2)

    def y_at(v):
        return margin_t + inner_h - (v - v_min) / (v_max - v_min) * inner_h

    points = [(x_at(i), y_at(v)) for i, v in enumerate(curve_values)]
    line_path = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    area_path = line_path + f" L {points[-1][0]:.1f},{margin_t + inner_h:.1f} L {points[0][0]:.1f},{margin_t + inner_h:.1f} Z"

    # gridlines: 4 horizontal ticks
    ticks = []
    for k in range(5):
        v = v_min + (v_max - v_min) * k / 4
        y = y_at(v)
        ticks.append((y, v))

    dots_svg = []
    for i, (x, y) in enumerate(points):
        dots_svg.append(
            f'<g class="pt-group" data-i="{i}">'
            f'<circle class="pt-hit" cx="{x:.1f}" cy="{y:.1f}" r="12"></circle>'
            f'<circle class="pt-visual" cx="{x:.1f}" cy="{y:.1f}" r="4"></circle>'
            f'</g>'
        )

    chart_points_json = json.dumps([
        {"label": curve_labels[i], "date": curve_dates[i], "equity": round(curve_values[i], 2), "pnl": round(curve_pnls[i], 2)}
        for i in range(n)
    ])

    end_color = "var(--good)" if curve_values[-1] >= curve_values[0] else "var(--critical)"

    gridlines_svg = "\n".join(
        f'<line x1="{margin_l}" y1="{y:.1f}" x2="{plot_w - margin_r}" y2="{y:.1f}" class="grid"></line>'
        f'<text x="{margin_l - 8}" y="{y:.1f}" class="axis-label" text-anchor="end" dominant-baseline="middle">{_fmt_money(v)}</text>'
        for y, v in ticks
    )

    # ---- stat tiles ----
    win_rate = stats["win_rate_pct"]
    avg_r = stats["avg_r_multiple"]
    tiles = "".join([
        _stat_tile("Trades taken", f"{stats['trades']}"),
        _stat_tile("Wins", f"{stats['wins']}", "good"),
        _stat_tile("Losses", f"{stats['losses']}", "critical"),
        _stat_tile("Breakeven", f"{stats['breakeven_or_flat']}"),
        _stat_tile("Win rate", f"{win_rate:.1f}%"),
        _stat_tile("Avg R multiple", f"{avg_r:+.2f}R", "good" if avg_r >= 0 else "critical"),
        _stat_tile("Profit factor", "∞" if stats["profit_factor"] == float("inf") else f"{stats['profit_factor']:.2f}"),
        _stat_tile("Total P&L", _fmt_money(stats["total_pnl"]), "good" if stats["total_pnl"] >= 0 else "critical"),
        _stat_tile("Return on account", _fmt_pct(stats["return_pct"]), "good" if stats["return_pct"] >= 0 else "critical"),
        _stat_tile("Ending equity", _fmt_money(stats["ending_equity"])),
        _stat_tile("Max drawdown", f"{_fmt_money(stats['max_drawdown'])} ({stats['max_drawdown_pct']:.2f}%)", "critical"),
    ])

    # ---- trade log rows ----
    outcome_label = {"win": "Win", "loss": "Loss", "be": "BE"}
    outcome_icon = {"win": "▲", "loss": "▼", "be": "–"}
    rows = []
    for _, t in df.iterrows():
        outcome = t["outcome"]
        row_class = f"row-{outcome}"
        be_badge = ' <span class="badge">moved to BE</span>' if t["moved_to_breakeven"] else ""
        rows.append(f"""
        <tr class="{row_class}">
          <td>{html.escape(str(t['date']))}</td>
          <td class="dir dir-{t['direction']}">{t['direction'].capitalize()}</td>
          <td class="mono">{html.escape(str(t['entry_time']))}</td>
          <td class="mono num">{t['entry_price']:.2f}</td>
          <td class="mono">{html.escape(str(t['exit_time']))}</td>
          <td class="mono num">{t['exit_price']:.2f}</td>
          <td>{html.escape(str(t['exit_reason']).replace('_', ' '))}{be_badge}</td>
          <td class="outcome outcome-{outcome}">{outcome_icon[outcome]} {outcome_label[outcome]}</td>
          <td class="mono num">{t['r_multiple']:+.2f}R</td>
          <td class="mono num pnl-{outcome}">{_fmt_money(t['pnl'])}</td>
          <td class="mono num">{_fmt_money(t['equity_after'])}</td>
        </tr>""")
    trade_rows_html = "".join(rows)

    subtitle = (
        f"{meta.get('symbol', '')} &middot; {meta.get('start', '')} to {meta.get('end', '')} &middot; "
        f"OR {meta.get('or_minutes', 15)}min &middot; {cfg.reward_r:.2g}R target &middot; "
        f"BE at {cfg.breakeven_r:.2g}R &middot; entries cut off {meta.get('cutoff_entry', '')} ET &middot; "
        f"flat {meta.get('flat_time', '')} ET"
    )

    html_doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>ORB Backtest Report — {html.escape(str(meta.get('symbol', '')))}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {{
    color-scheme: light;
    --surface-1:      #fcfcfb;
    --page:           #f9f9f7;
    --text-primary:   #0b0b0b;
    --text-secondary: #52514e;
    --text-muted:     #898781;
    --grid:           #e1e0d9;
    --baseline:       #c3c2b7;
    --border:         rgba(11,11,11,0.10);
    --blue:           #2a78d6;
    --blue-wash:      rgba(42,120,214,0.10);
    --good:           #0ca30c;
    --critical:       #d03b3b;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:where(:not([data-theme="light"])) {{
      color-scheme: dark;
      --surface-1:      #1a1a19;
      --page:           #0d0d0d;
      --text-primary:   #ffffff;
      --text-secondary: #c3c2b7;
      --text-muted:     #898781;
      --grid:           #2c2c2a;
      --baseline:       #383835;
      --border:         rgba(255,255,255,0.10);
      --blue:           #3987e5;
      --blue-wash:      rgba(57,135,229,0.14);
      --good:           #0ca30c;
      --critical:       #e66767;
    }}
  }}
  :root[data-theme="dark"] {{
    color-scheme: dark;
    --surface-1:      #1a1a19;
    --page:           #0d0d0d;
    --text-primary:   #ffffff;
    --text-secondary: #c3c2b7;
    --text-muted:     #898781;
    --grid:           #2c2c2a;
    --baseline:       #383835;
    --border:         rgba(255,255,255,0.10);
    --blue:           #3987e5;
    --blue-wash:      rgba(57,135,229,0.14);
    --good:           #0ca30c;
    --critical:       #e66767;
  }}

  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    padding: 32px 20px 64px;
    background: var(--page);
    color: var(--text-primary);
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  }}
  .wrap {{ max-width: 1080px; margin: 0 auto; }}
  h1 {{ font-size: 22px; font-weight: 650; margin: 0 0 4px; }}
  .subtitle {{ color: var(--text-secondary); font-size: 13px; margin: 0 0 28px; }}

  .card {{
    background: var(--surface-1);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px;
    margin-bottom: 24px;
    overflow-x: auto;
  }}
  .card h2 {{ font-size: 14px; font-weight: 650; margin: 0 0 16px; color: var(--text-primary); }}

  .tiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 1px; background: var(--border); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; margin-bottom: 24px; }}
  .tile {{ background: var(--surface-1); padding: 16px 18px; }}
  .tile-label {{ font-size: 12px; color: var(--text-secondary); margin-bottom: 6px; }}
  .tile-value {{ font-size: 22px; font-weight: 650; font-variant-numeric: proportional-nums; }}
  .tile-value.tone-good {{ color: var(--good); }}
  .tile-value.tone-critical {{ color: var(--critical); }}

  .chart-wrap {{ position: relative; }}
  svg.chart {{ width: 100%; height: auto; display: block; overflow: visible; }}
  .grid {{ stroke: var(--grid); stroke-width: 1; }}
  .axis-label {{ fill: var(--text-muted); font-size: 10px; }}
  .area {{ fill: var(--blue-wash); }}
  .line {{ fill: none; stroke: var(--blue); stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }}
  .pt-hit {{ fill: transparent; cursor: crosshair; }}
  .pt-visual {{ fill: var(--blue); fill-opacity: 0; stroke: var(--surface-1); stroke-width: 2; pointer-events: none; }}
  .pt-group.hot .pt-visual {{ fill-opacity: 1; }}
  .end-label {{ fill: var(--text-primary); font-size: 12px; font-weight: 650; }}
  .crosshair {{ stroke: var(--baseline); stroke-width: 1; stroke-dasharray: 3 3; opacity: 0; pointer-events: none; }}
  .tooltip {{
    position: absolute; pointer-events: none; opacity: 0;
    background: var(--surface-1); border: 1px solid var(--border); border-radius: 8px;
    padding: 8px 10px; font-size: 12px; color: var(--text-primary); box-shadow: 0 4px 16px rgba(0,0,0,0.15);
    transform: translate(-50%, -110%); white-space: nowrap; transition: opacity 0.08s ease;
  }}
  .tooltip .t-label {{ color: var(--text-secondary); font-size: 11px; margin-bottom: 2px; }}
  .tooltip .t-pnl.pos {{ color: var(--good); }}
  .tooltip .t-pnl.neg {{ color: var(--critical); }}

  table {{ width: 100%; border-collapse: collapse; font-size: 13px; min-width: 880px; }}
  th {{ text-align: left; font-size: 11px; text-transform: uppercase; letter-spacing: 0.03em; color: var(--text-muted); font-weight: 600; padding: 8px 10px; border-bottom: 1px solid var(--grid); white-space: nowrap; }}
  td {{ padding: 8px 10px; border-bottom: 1px solid var(--grid); white-space: nowrap; }}
  tr:last-child td {{ border-bottom: none; }}
  .mono {{ font-variant-numeric: tabular-nums; }}
  .num {{ text-align: right; }}
  .dir-long {{ color: var(--good); }}
  .dir-short {{ color: var(--critical); }}
  .outcome {{ font-weight: 600; }}
  .outcome-win {{ color: var(--good); }}
  .outcome-loss {{ color: var(--critical); }}
  .outcome-be {{ color: var(--text-secondary); }}
  .pnl-win {{ color: var(--good); }}
  .pnl-loss {{ color: var(--critical); }}
  .pnl-be {{ color: var(--text-secondary); }}
  .badge {{ font-size: 10px; color: var(--text-muted); border: 1px solid var(--border); border-radius: 6px; padding: 1px 5px; margin-left: 4px; }}

  footer {{ color: var(--text-muted); font-size: 12px; margin-top: 12px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>ORB Backtest Report</h1>
  <p class="subtitle">{subtitle}</p>

  <div class="tiles">{tiles}</div>

  <div class="card">
    <h2>Equity curve</h2>
    <div class="chart-wrap" id="chart-wrap">
      <svg class="chart" viewBox="0 0 {plot_w} {plot_h}" id="equity-svg">
        {gridlines_svg}
        <path class="area" d="{area_path}"></path>
        <path class="line" d="{line_path}"></path>
        {''.join(dots_svg)}
        <text class="end-label" x="{points[-1][0] - 4:.1f}" y="{points[-1][1] - 10:.1f}" text-anchor="end" fill="{end_color}">{_fmt_money(curve_values[-1])}</text>
        <line class="crosshair" id="crosshair" x1="0" y1="{margin_t}" x2="0" y2="{margin_t + inner_h}"></line>
      </svg>
      <div class="tooltip" id="tooltip">
        <div class="t-label" id="tt-label"></div>
        <div id="tt-equity"></div>
        <div id="tt-pnl"></div>
      </div>
    </div>
  </div>

  <div class="card">
    <h2>Trade log</h2>
    <table>
      <thead>
        <tr>
          <th>Date</th><th>Dir</th><th>Entry time</th><th class="num">Entry</th>
          <th>Exit time</th><th class="num">Exit</th><th>Exit reason</th><th>Outcome</th>
          <th class="num">R</th><th class="num">P&amp;L</th><th class="num">Equity after</th>
        </tr>
      </thead>
      <tbody>{trade_rows_html}</tbody>
    </table>
  </div>

  <footer>Generated from {stats['trades']} trade(s) &middot; starting equity {_fmt_money(cfg.account_size)} &middot; risk {cfg.risk_pct:.2g}% per trade &middot; tick value ${cfg.tick_value:.2f}</footer>
</div>

<script>
(function() {{
  var points = {chart_points_json};
  var svg = document.getElementById('equity-svg');
  var tooltip = document.getElementById('tooltip');
  var crosshair = document.getElementById('crosshair');
  var wrap = document.getElementById('chart-wrap');
  var groups = svg.querySelectorAll('.pt-group');
  var ttLabel = document.getElementById('tt-label');
  var ttEquity = document.getElementById('tt-equity');
  var ttPnl = document.getElementById('tt-pnl');

  function showPoint(i, group) {{
    groups.forEach(function(g) {{ g.classList.remove('hot'); }});
    group.classList.add('hot');
    var p = points[i];
    var visual = group.querySelector('.pt-visual');
    var cx = visual.getAttribute('cx');
    crosshair.setAttribute('x1', cx);
    crosshair.setAttribute('x2', cx);
    crosshair.style.opacity = 1;

    ttLabel.textContent = p.label + (p.date ? ' — ' + p.date : '');
    ttEquity.textContent = 'Equity: $' + p.equity.toLocaleString(undefined, {{minimumFractionDigits:2, maximumFractionDigits:2}});
    ttPnl.innerHTML = '';
    if (i > 0) {{
      var pnlSpan = document.createElement('span');
      pnlSpan.className = 't-pnl ' + (p.pnl >= 0 ? 'pos' : 'neg');
      pnlSpan.textContent = 'Trade P&L: ' + (p.pnl >= 0 ? '+' : '-') + '$' + Math.abs(p.pnl).toLocaleString(undefined, {{minimumFractionDigits:2, maximumFractionDigits:2}});
      ttPnl.appendChild(pnlSpan);
    }}

    var svgRect = svg.getBoundingClientRect();
    var wrapRect = wrap.getBoundingClientRect();
    var scaleX = svgRect.width / {plot_w};
    var scaleY = svgRect.height / {plot_h};
    var left = (svgRect.left - wrapRect.left) + parseFloat(cx) * scaleX;
    var top = (svgRect.top - wrapRect.top) + parseFloat(visual.getAttribute('cy')) * scaleY;
    tooltip.style.left = left + 'px';
    tooltip.style.top = top + 'px';
    tooltip.style.opacity = 1;
  }}

  function hide() {{
    groups.forEach(function(g) {{ g.classList.remove('hot'); }});
    crosshair.style.opacity = 0;
    tooltip.style.opacity = 0;
  }}

  groups.forEach(function(group) {{
    var i = parseInt(group.getAttribute('data-i'), 10);
    group.addEventListener('mouseenter', function() {{ showPoint(i, group); }});
  }});
  svg.addEventListener('mouseleave', hide);
}})();
</script>
</body>
</html>
"""
    out_path.write_text(html_doc)

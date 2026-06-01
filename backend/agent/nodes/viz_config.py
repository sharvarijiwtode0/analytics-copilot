"""
Node 6: Visualization Config Generation
Generates a complete Apache ECharts option object from query results.
The frontend renders this directly — no chart logic on the client side.
"""
from __future__ import annotations
import json
import structlog
from backend.agent.state import AnalyticsState
from backend.agent.llm import call_llm

log = structlog.get_logger(__name__)

# ECharts color palette
COLORS = ["#5470c6", "#91cc75", "#fac858", "#ee6666", "#73c0de",
          "#3ba272", "#fc8452", "#9a60b4", "#ea7ccc"]

# Chart type → ECharts series type mapping
CHART_TYPE_MAP = {
    "bar": "bar", "line": "line", "area": "line", "pie": "pie",
    "scatter": "scatter", "heatmap": "heatmap", "gauge": "gauge",
    "funnel": "funnel", "radar": "radar", "table": "table",
}


def _pivot_and_build_multi_series(columns: list, rows: list, title: str, chart_type: str = "bar", question: str = "") -> dict:
    """
    Pivot multi-dimensional query results into a multi-series ECharts option.
    Handles:
    - Multiple numeric columns (e.g. month, skincare, makeup) -> One series per numeric column.
    - Two categorical + one numeric (e.g. platform, month, revenue) -> Pivots to unique platforms as series, months on X-axis.
    """
    if not columns or not rows:
        return {}

    # 1. Classify columns
    num_cols = []
    str_cols = []
    
    for col in columns:
        is_num = True
        has_val = False
        for r in rows[:20]:
            val = r.get(col) if isinstance(r, dict) else None
            if val is not None and val != "":
                has_val = True
                try:
                    float(str(val).replace(',', '').replace('₹', '').replace('$', '').strip())
                except ValueError:
                    is_num = False
                    break
        if is_num and has_val:
            num_cols.append(col)
        else:
            str_cols.append(col)

    if not num_cols:
        num_cols = [c for c in columns[1:]]
        str_cols = [columns[0]]
    if not str_cols:
        str_cols = [columns[0]]
        num_cols = [c for c in columns[1:]]

    # --- CASE 1: Multiple numeric columns ---
    if len(str_cols) == 1 and len(num_cols) >= 1:
        x_col = str_cols[0]

        # ── Smart column filtering: drop metrics the user did NOT ask for ──
        # If the user only asked for revenue/sales → drop units columns
        # If the user only asked for units/orders  → drop revenue columns
        q_lower = question.lower()
        REVENUE_WORDS = {"revenue", "sales", "earning", "income", "amount", "price", "spend", "value", "subtotal", "profit"}
        UNIT_WORDS    = {"unit", "qty", "quantity", "volume", "order", "count", "sold"}
        asked_revenue = any(w in q_lower for w in REVENUE_WORDS)
        asked_units   = any(w in q_lower for w in UNIT_WORDS)

        # "trend" / "performance" / "monthly sales" implicitly asks for revenue only
        if ("trend" in q_lower or "performance" in q_lower) and not asked_units:
            asked_revenue = True

        if len(num_cols) > 1 and (asked_revenue or asked_units) and not (asked_revenue and asked_units):
            # Keep only columns that match what was asked
            def _is_revenue_col(c):
                return any(w in c.lower() for w in ["revenue", "sales", "subtotal", "income", "amount", "price", "value"])
            def _is_unit_col(c):
                return any(w in c.lower() for w in ["unit", "qty", "quantity", "order", "count", "sold", "volume"])

            if asked_revenue and not asked_units:
                # Keep only revenue columns (first if none match)
                filtered = [c for c in num_cols if _is_revenue_col(c)]
                num_cols = filtered if filtered else [num_cols[0]]
            elif asked_units and not asked_revenue:
                # Keep only unit columns (first if none match)
                filtered = [c for c in num_cols if _is_unit_col(c)]
                num_cols = filtered if filtered else [num_cols[0]]

        x_data = []
        seen_x = set()
        for r in rows:
            val = str(r.get(x_col, "") if isinstance(r, dict) else r[0])
            if val not in seen_x:
                seen_x.add(val)
                x_data.append(val)
        
        series_list = []
        for n_col in num_cols:
            y_data = []
            for x_val in x_data:
                row_val = 0
                for r in rows:
                    if str(r.get(x_col, "") if isinstance(r, dict) else r[0]) == x_val:
                        val = r.get(n_col) if isinstance(r, dict) else r[columns.index(n_col)]
                        try:
                            row_val = round(float(str(val).replace(',', '').replace('₹', '').replace('$', '').strip()), 2) if val is not None else 0
                        except:
                            row_val = 0
                        break
                y_data.append(row_val)
            
            series_list.append({
                "name": n_col,
                "type": chart_type,
                "data": y_data,
                "smooth": chart_type == "line",
                "areaStyle": {"opacity": 0.05} if chart_type == "line" else None
            })

        # Check if we should use dual Y-axes
        # If there are exactly two numeric series, and one represents currency/revenue while the other represents count/units
        use_dual_axis = False
        y_axes = []
        if len(series_list) == 2:
            s1_name, s2_name = series_list[0]["name"].lower(), series_list[1]["name"].lower()
            s1_rev = any(w in s1_name for w in ["revenue", "sales", "price", "amount", "subtotal", "income", "value"])
            s2_units = any(w in s2_name for w in ["unit", "qty", "quantity", "order", "count", "sold", "volume"])
            
            s2_rev = any(w in s2_name for w in ["revenue", "sales", "price", "amount", "subtotal", "income", "value"])
            s1_units = any(w in s1_name for w in ["unit", "qty", "quantity", "order", "count", "sold", "volume"])
            
            if (s1_rev and s2_units) or (s2_rev and s1_units):
                use_dual_axis = True
                
        if use_dual_axis:
            # First axis is currency, second is counts
            s1_is_rev = any(w in series_list[0]["name"].lower() for w in ["revenue", "sales", "price", "amount", "subtotal", "income", "value"])
            
            y_axes = [
                {
                    "type": "value",
                    "name": "Revenue",
                    "axisLabel": {"formatter": "₹{value}"}
                },
                {
                    "type": "value",
                    "name": "Volume",
                    "axisLabel": {"formatter": "{value}"},
                    "splitLine": {"show": False}
                }
            ]
            # Map the series to correct Y-axis indexes
            series_list[0]["yAxisIndex"] = 0 if s1_is_rev else 1
            series_list[1]["yAxisIndex"] = 1 if s1_is_rev else 0
            
            # Make the volume series a line chart, and revenue a bar chart
            series_list[0]["type"] = "bar" if s1_is_rev else "line"
            series_list[1]["type"] = "line" if s1_is_rev else "bar"
        else:
            is_currency = any(
                any(kw in s["name"].lower() for kw in ["revenue", "sales", "price", "amount", "subtotal", "income", "value"])
                for s in series_list
            )
            y_axes = {
                "type": "value",
                "axisLabel": {"formatter": "₹{value}" if is_currency else "{value}"}
            }

        # Scrollable Zoom overlay if categories are large (> 12)
        zoom = []
        if len(x_data) > 12:
            zoom = [{
                "type": "slider",
                "show": True,
                "start": 0,
                "end": max(10, int(1200 / len(x_data))),
                "bottom": "5%"
            }]

        return {
            "title": {"text": title, "left": "center"},
            "color": COLORS,
            "tooltip": {"trigger": "axis"},
            "legend": {"top": "8%", "show": len(series_list) > 1},
            "xAxis": {
                "type": "category",
                "data": x_data,
                "axisLabel": {
                    "rotate": 30 if len(x_data) > 6 else 0,
                    "interval": 0,  # Show all labels
                    "overflow": "truncate",
                    "width": 80,  # Max width before truncation
                    "ellipsis": "..."
                }
            },
            "yAxis": y_axes,
            "series": series_list,
            "grid": {"left": "12%", "right": "8%" if use_dual_axis else "5%", "bottom": "35%" if len(x_data) > 12 else ("25%" if len(x_data) > 6 else "15%"), "top": "18%"},
            "dataZoom": zoom
        }

    # --- CASE 2: Two categorical + one numeric ---
    if len(str_cols) >= 2 and len(num_cols) == 1:
        time_indicators = ["month", "date", "year", "day", "time", "week"]
        x_col = str_cols[1]
        series_col = str_cols[0]
        
        for s in str_cols:
            if any(ti in s.lower() for ti in time_indicators):
                x_col = s
                series_col = [c for c in str_cols if c != s][0]
                break

        val_col = num_cols[0]

        x_data = []
        seen_x = set()
        for r in rows:
            val = str(r.get(x_col, "") if isinstance(r, dict) else r[columns.index(x_col)])
            if val not in seen_x:
                seen_x.add(val)
                x_data.append(val)
        
        try:
            x_data.sort()
        except:
            pass

        series_cats = []
        seen_cat = set()
        for r in rows:
            val = str(r.get(series_col, "") if isinstance(r, dict) else r[columns.index(series_col)])
            if val not in seen_cat:
                seen_cat.add(val)
                series_cats.append(val)

        series_list = []
        for cat in series_cats:
            y_data = []
            for x_val in x_data:
                row_val = 0
                for r in rows:
                    rcat = str(r.get(series_col, "") if isinstance(r, dict) else r[columns.index(series_col)])
                    rx = str(r.get(x_col, "") if isinstance(r, dict) else r[columns.index(x_col)])
                    if rcat == cat and rx == x_val:
                        val = r.get(val_col) if isinstance(r, dict) else r[columns.index(val_col)]
                        try:
                            row_val = round(float(str(val).replace(',', '').replace('₹', '').replace('$', '').strip()), 2) if val is not None else 0
                        except:
                            row_val = 0
                        break
                y_data.append(row_val)

            series_list.append({
                "name": cat,
                "type": chart_type,
                "data": y_data,
                "smooth": chart_type == "line",
                "areaStyle": {"opacity": 0.05} if chart_type == "line" else None
            })

        return {
            "title": {"text": title, "left": "center"},
            "color": COLORS,
            "tooltip": {"trigger": "axis"},
            "legend": {"top": "8%", "show": len(series_list) > 1},
            "xAxis": {
                "type": "category",
                "data": x_data,
                "axisLabel": {
                    "rotate": 30 if len(x_data) > 6 else 0,
                    "interval": 0,  # Show all labels
                    "overflow": "truncate",
                    "width": 80,
                    "ellipsis": "..."
                }
            },
            "yAxis": {"type": "value"},
            "series": series_list,
            "grid": {"left": "12%", "right": "5%", "bottom": "25%" if len(x_data) > 6 else "15%", "top": "18%"},
        }

    # --- FALLBACK ---
    x_col = columns[0]
    y_col = num_cols[0] if num_cols else (columns[1] if len(columns) > 1 else columns[0])
    
    x_data = [str(r.get(x_col, "") if isinstance(r, dict) else r[0]) for r in rows[:50]]
    y_data = []
    for r in rows[:50]:
        val = r.get(y_col) if isinstance(r, dict) else (r[columns.index(y_col)] if y_col in columns else r[0])
        try:
            y_data.append(round(float(str(val).replace(',', '').replace('₹', '').replace('$', '').strip()), 2) if val is not None else 0)
        except:
            y_data.append(0)

    return {
        "title": {"text": title, "left": "center"},
        "color": COLORS,
        "tooltip": {"trigger": "axis"},
        "xAxis": {
            "type": "category",
            "data": x_data,
            "axisLabel": {
                "rotate": 30 if len(x_data) > 6 else 0,
                "interval": 0,  # Show all labels
                "overflow": "truncate",
                "width": 80,
                "ellipsis": "..."
            }
        },
        "yAxis": {"type": "value"},
        "series": [{"name": y_col, "type": chart_type, "data": y_data, "smooth": chart_type == "line",
                    "areaStyle": {"opacity": 0.05} if chart_type == "line" else None}],
        "grid": {"left": "12%", "right": "5%", "bottom": "25%" if len(x_data) > 6 else "15%"},
    }


def _build_bar_chart(columns: list, rows: list, title: str, question: str = "") -> dict:
    return _pivot_and_build_multi_series(columns, rows, title, "bar", question)


def _build_line_chart(columns: list, rows: list, title: str, question: str = "") -> dict:
    return _pivot_and_build_multi_series(columns, rows, title, "line", question)


def _build_pie_chart(columns: list, rows: list, title: str) -> dict:
    if len(columns) < 2:
        return {}
    name_col, val_col = columns[0], columns[1]
    data = []
    for r in rows[:40]:
        name = str(r.get(name_col, "") if isinstance(r, dict) else r[0])
        val_raw = r.get(val_col) if isinstance(r, dict) else r[1]
        try:
            data.append({"name": name, "value": round(float(val_raw or 0), 2)})
        except (ValueError, TypeError):
            pass

    # High Cardinality Pie Chart Grouping (Adaptive Presentation Agent pattern)
    if len(data) > 8:
        data = sorted(data, key=lambda x: x["value"], reverse=True)
        top_data = data[:7]
        other_sum = sum(x["value"] for x in data[7:])
        if other_sum > 0:
            top_data.append({"name": "Other", "value": round(other_sum, 2)})
        data = top_data

    # Use simpler labels for small screens, detailed for larger
    label_formatter = "{b}\n{d}%" if len(data) <= 6 else "{d}%"

    return {
        "title": {"text": title, "left": "center", "textStyle": {"fontSize": 14}},
        "color": COLORS,
        "tooltip": {"trigger": "item", "formatter": "{b}: ₹{c} ({d}%)"},
        "legend": {"orient": "horizontal" if len(data) > 6 else "vertical",
                   "left": "center" if len(data) > 6 else "left",
                   "top": "bottom" if len(data) > 6 else "middle",
                   "type": "scroll" if len(data) > 10 else "plain"},
        "series": [{
            "type": "pie",
            "radius": ["35%", "65%"],
            "center": ["50%", "45%"],
            "data": data,
            "label": {
                "show": True,
                "formatter": label_formatter,
                "fontSize": 11,
                "overflow": "truncate",
                "ellipsis": "..."
            },
            "emphasis": {
                "label": {"show": True, "fontSize": 14, "fontWeight": "bold"}
            }
        }],
        "grid": {"left": "5%", "right": "5%", "bottom": "20%" if len(data) > 6 else "5%", "top": "10%"},
    }


def _build_scatter_chart(columns: list, rows: list, title: str) -> dict:
    if len(columns) < 2:
        return {}
    x_col, y_col = columns[0], columns[1]
    data = []
    for r in rows[:200]:
        try:
            xv = float(r.get(x_col, 0) if isinstance(r, dict) else r[0])
            yv = float(r.get(y_col, 0) if isinstance(r, dict) else r[1])
            data.append([xv, yv])
        except (ValueError, TypeError):
            pass

    return {
        "title": {"text": title, "left": "center"},
        "tooltip": {"trigger": "item"},
        "xAxis": {"name": x_col},
        "yAxis": {"name": y_col},
        "series": [{"type": "scatter", "data": data, "symbolSize": 8}],
    }


def _build_funnel_chart(columns: list, rows: list, title: str) -> dict:
    if len(columns) < 2:
        return {}
    
    # Try to find the first numeric column for values, first string for names
    name_col = next((c for c in columns if 'status' in c.lower() or 'stage' in c.lower()), columns[0])
    val_col = next((c for c in columns if 'count' in c.lower() or 'units' in c.lower() or 'orders' in c.lower()), columns[1])

    def _safe_float(v):
        if v is None: return 0
        if isinstance(v, (int, float)): return float(v)
        # Strip commas, currency symbols, and spaces
        try:
            clean = str(v).replace(',', '').replace('₹', '').replace('$', '').strip()
            return float(clean)
        except: return 0

    data = []
    # Sort rows by the value column safely
    try:
        sorted_rows = sorted(rows, key=lambda r: _safe_float(r.get(val_col) if isinstance(r, dict) else r[1]), reverse=True)
    except:
        sorted_rows = rows[:10]

    for r in sorted_rows[:12]:
        name = str(r.get(name_col, "") if isinstance(r, dict) else r[0])
        val = _safe_float(r.get(val_col) if isinstance(r, dict) else r[1])
        if val > 0:
            data.append({"name": name, "value": val})

    return {
        "title": {"text": title, "left": "center"},
        "tooltip": {"trigger": "item", "formatter": "{b} : {c}"},
        "legend": {"orient": "vertical", "left": "left", "top": "15%"},
        "series": [
            {
                "name": "Conversion",
                "type": "funnel",
                "left": "15%",
                "top": "15%",
                "bottom": "10%",
                "width": "70%",
                "min": 0,
                "label": {"show": True, "position": "inside"},
                "data": data,
            }
        ]
    }


def _build_gauge_chart(columns: list, rows: list, title: str) -> dict:
    # Gauge usually shows a single value vs a target
    val = 0
    name = "KPI"
    if rows:
        r = rows[0]
        name = str(columns[0])
        val_raw = r.get(columns[0]) if isinstance(r, dict) else r[0]
        try:
            val = round(float(val_raw or 0), 2)
        except (ValueError, TypeError):
            pass

    return {
        "title": {"text": title, "left": "center"},
        "series": [
            {
                "name": name,
                "type": "gauge",
                "detail": {"formatter": "{value}"},
                "data": [{"value": val, "name": name}]
            }
        ]
    }


def _build_heatmap_chart(columns: list, rows: list, title: str) -> dict:
    if len(columns) < 3:
        return _build_bar_chart(columns, rows, title)
    
    x_col, y_col, val_col = columns[0], columns[1], columns[2]
    x_labs = list(dict.fromkeys([str(r.get(x_col) if isinstance(r, dict) else r[0]) for r in rows]))
    y_labs = list(dict.fromkeys([str(r.get(y_col) if isinstance(r, dict) else r[1]) for r in rows]))
    
    data = []
    max_val = 0
    for r in rows:
        try:
            x_idx = x_labs.index(str(r.get(x_col) if isinstance(r, dict) else r[0]))
            y_idx = y_labs.index(str(r.get(y_col) if isinstance(r, dict) else r[1]))
            v = float(r.get(val_col, 0) if isinstance(r, dict) else r[2])
            data.append([x_idx, y_idx, v])
            max_val = max(max_val, v)
        except (ValueError, TypeError):
            pass

    return {
        "title": {"text": title, "left": "center"},
        "tooltip": {"position": "top"},
        "xAxis": {"type": "category", "data": x_labs},
        "yAxis": {"type": "category", "data": y_labs},
        "visualMap": {"min": 0, "max": max_val, "calculable": True, "orient": "horizontal", "left": "center", "bottom": "0%"},
        "series": [{"name": val_col, "type": "heatmap", "data": data, "label": {"show": True}}]
    }


def _build_table_config(columns: list, rows: list, title: str) -> dict:
    """Table view — no ECharts needed, just structured data."""
    return {
        "type": "table",
        "title": title,
        "columns": columns,
        "rows": rows[:200],
        "total_rows": len(rows),
    }


CHART_BUILDERS = {
    "bar": _build_bar_chart,
    "line": _build_line_chart,
    "area": _build_line_chart,
    "pie": _build_pie_chart,
    "scatter": _build_scatter_chart,
    "funnel": _build_funnel_chart,
    "gauge": _build_gauge_chart,
    "heatmap": _build_heatmap_chart,
    "table": _build_table_config,
}


async def generate_viz_config(state: AnalyticsState) -> AnalyticsState:
    query_results = state.get("query_results", {})
    intent = state.get("intent", {})
    question = state["user_question"]
    key_metrics = state.get("key_metrics", {})

    rows = query_results.get("rows", [])
    columns = query_results.get("columns", [])

    if not rows:
        return {"viz_config": {}, "viz_type": "table"}

    # Suppress charts for qualitative/analytical questions (prefer point format/text only)
    qualitative_keywords = ["reason", "factor", "cause", "why", "explain", "influence", "driver"]
    if any(k in question.lower() for k in qualitative_keywords):
        log.info("viz.suppressed_for_qualitative_query", question=question)
        return {"viz_config": None, "viz_type": None}

    # Suppress charts for single month queries (if exactly 1 month is asked, return only text/insights)
    import re
    month_names = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december",
                   "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    q_lower = question.lower()
    has_month_word = any(re.search(r'\b' + m + r'\b', q_lower) for m in month_names)
    has_digit_month = re.search(r'\b(0[1-9]|1[0-2])[-/](20[12]\d)\b', q_lower) or re.search(r'\b(20[12]\d)[-/](0[1-9]|1[0-2])\b', q_lower)
    
    is_trend_query = any(w in q_lower for w in ["trend", "daily", "weekly", "chart", "graph", "plot", "map", "viz", "visualization"])
    if (has_month_word or has_digit_month) and len(rows) == 1 and not is_trend_query:
        log.info("viz.suppressed_for_single_month", question=question)
        return {"viz_config": None, "viz_type": None}

    # Step 1: Determine chart type
    chart_hint = intent.get("chart_type_hint")
    viz_type = chart_hint or _auto_select_chart_type(columns, rows, intent, question)

    # Step 2: Generate title
    title = _generate_title(question, viz_type)

    # Step 3: Build ECharts config (pass question so builder can filter unused metrics)
    builder = CHART_BUILDERS.get(viz_type, _build_table_config)
    try:
        if viz_type in ("bar", "line", "area"):
            viz_config = builder(columns, rows, title, question)
        else:
            viz_config = builder(columns, rows, title)
    except Exception as exc:
        log.warning("viz.build_failed", chart_type=viz_type, error=str(exc))
        viz_config = _build_table_config(columns, rows, title)
        viz_type = "table"

    # Step 4: Enhance with insights if available
    if key_metrics and viz_type != "table":
        viz_config["graphic"] = []  # Could add annotation overlays here

    log.info("viz.generated", type=viz_type, columns=len(columns))
    return {"viz_config": viz_config, "viz_type": viz_type}


def _auto_select_chart_type(columns: list, rows: list, intent: dict, question: str = "") -> str:
    """Heuristic chart type selection based on data shape, intent, and question text."""
    intent_type = intent.get("type", "")
    row_count = len(rows)
    q = question.lower()

    # Rule-based overrides based on explicit user keyword specifications
    if "line chart" in q or "line graph" in q or "lines" in q:
        return "line"
    if "bar chart" in q or "bar graph" in q or "bars" in q:
        return "bar"
    if "pie chart" in q or "pie" in q:
        return "pie"
    if "funnel" in q or "conversion funnel" in q:
        return "funnel"
    if "gauge" in q or "meter" in q:
        return "gauge"
    if "heatmap" in q or "heat map" in q:
        return "heatmap"
    if "scatter" in q or "bubble" in q:
        return "scatter"
    if "table" in q or "grid" in q or "tabular" in q:
        return "table"

    if intent_type == "trend_analysis" or "time" in str(columns).lower() or "date" in str(columns).lower():
        return "line"

    if "funnel" in q or "conversion" in q or "stages" in q:
        return "funnel"

    # A gauge chart is only relatable for explicit gauge requests, or target completion percentages/rates.
    # It should NEVER be returned for simple scalar numbers like total counts, sums, or general row counts.
    is_gauge_relatable = (
        "gauge" in q or 
        (any(w in q for w in ["target", "progress", "achievement", "completion", "rate", "percentage", "%"]) and row_count == 1)
    )
    if is_gauge_relatable:
        return "gauge"

    if len(columns) == 3 and row_count > 10:
        return "heatmap"

    if row_count <= 8 and len(columns) == 2:
        return "pie"

    if len(columns) >= 2:
        return "bar"

    return "table"


def _generate_title(question: str, viz_type: str) -> str:
    # Truncate long questions for chart titles
    q = question.strip().rstrip("?")
    if len(q) > 60:
        q = q[:57] + "..."
    return q

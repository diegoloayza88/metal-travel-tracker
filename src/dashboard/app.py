"""
dashboard/app.py
----------------
Dashboard principal de Metal Travel Tracker.

Ejecutar localmente:
    streamlit run src/dashboard/app.py

Variables de entorno necesarias:
    AWS_REGION=us-east-1
    DYNAMODB_TABLE_CONCERTS=metal-travel-tracker-prod-concerts
    AWS_PROFILE=<tu perfil> (o AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY)
"""

import os
import sys

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# Asegurar que src/ esté en el path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.dashboard.data.concerts import (
    ALL_COUNTRIES,
    COUNTRY_NAMES,
    get_all_concerts,
    get_concert_stats,
    get_festivals,
)
from src.dashboard.data.flights import get_all_routes_history, get_budget_table
from src.dashboard.data.orchestrator import get_last_runs, trigger_orchestrator

# ──────────────────────────────────────────────────────────────────────────────
# Config página
# ──────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="🤘 Metal Travel Tracker",
    page_icon="🤘",
    layout="wide",
    initial_sidebar_state="expanded",
)

# CSS personalizado
st.markdown("""
<style>
    .metric-card {
        background: #1a1a2e;
        border: 1px solid #e94560;
        border-radius: 8px;
        padding: 16px;
        text-align: center;
    }
    .watchlist-badge {
        background: #e94560;
        color: white;
        padding: 2px 8px;
        border-radius: 4px;
        font-size: 0.75rem;
        font-weight: bold;
    }
    .festival-badge {
        background: #533483;
        color: white;
        padding: 2px 8px;
        border-radius: 4px;
        font-size: 0.75rem;
    }
    [data-testid="stSidebar"] {
        background-color: #0f3460;
    }
</style>
""", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────────────────────
# Sidebar — navegación y filtros globales
# ──────────────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("🤘 Metal Travel")
    st.caption("Dashboard personal · Lima → El Mundo")
    st.divider()

    page = st.radio(
        "Sección",
        ["🗺️ Conciertos", "🎪 Festivales", "✈️ Vuelos & Precios", "💰 Presupuesto", "⚙️ Control"],
        label_visibility="collapsed",
    )

    st.divider()
    st.caption("Filtros globales")

    selected_countries = st.multiselect(
        "Países",
        options=ALL_COUNTRIES,
        default=ALL_COUNTRIES,
        format_func=lambda x: COUNTRY_NAMES.get(x, x),
    )

    days_ahead = st.slider("Meses a futuro", min_value=1, max_value=12, value=12) * 30

    watchlist_only = st.checkbox("Solo watchlist 🔥", value=False)

# ──────────────────────────────────────────────────────────────────────────────
# Cargar datos (con cache para no spammear DynamoDB)
# ──────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)  # 5 minutos de cache
def load_concerts(countries, days, watchlist):
    return get_all_concerts(
        countries=countries,
        days_ahead=days,
        min_confidence=0.5,
        watchlist_only=watchlist,
    )


@st.cache_data(ttl=300)
def load_festivals(days):
    return get_festivals(days_ahead=days)


@st.cache_data(ttl=300)
def load_stats():
    return get_concert_stats()


@st.cache_data(ttl=600)
def load_budget():
    return get_budget_table()


@st.cache_data(ttl=600)
def load_flight_history():
    return get_all_routes_history(lookback_days=90)


# ──────────────────────────────────────────────────────────────────────────────
# Header con métricas globales
# ──────────────────────────────────────────────────────────────────────────────

stats = load_stats()
concerts = load_concerts(selected_countries, days_ahead, watchlist_only)
df = pd.DataFrame(concerts)

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("🎸 Total conciertos", stats["total"])
col2.metric("🔥 Watchlist matches", stats["watchlist_hits"])
col3.metric("🎪 En festivales", stats["festival_concerts"])
col4.metric("🌍 Países con datos", stats["countries_with_data"])
col5.metric("📅 Próximos (filtro)", len(df))

st.divider()

# ──────────────────────────────────────────────────────────────────────────────
# PÁGINA: CONCIERTOS
# ──────────────────────────────────────────────────────────────────────────────

if page == "🗺️ Conciertos":
    st.header("🗺️ Conciertos")

    if df.empty:
        st.info("No hay conciertos con los filtros actuales.")
    else:
        # Sub-filtros de la página
        col_f1, col_f2, col_f3 = st.columns(3)
        with col_f1:
            search = st.text_input("🔍 Buscar banda / ciudad", "")
        with col_f2:
            min_score = st.selectbox(
                "Score mínimo watchlist",
                [0, 8, 10],
                format_func=lambda x: {0: "Todos", 8: "Parcial (8+)", 10: "Exacto (10)"}[x],
            )
        with col_f3:
            source_filter = st.multiselect(
                "Fuente",
                options=df["fuente"].unique().tolist() if not df.empty else [],
                default=df["fuente"].unique().tolist() if not df.empty else [],
            )

        # Aplicar filtros
        filtered = df.copy()
        if search:
            mask = (
                filtered["banda"].str.contains(search, case=False, na=False)
                | filtered["ciudad"].str.contains(search, case=False, na=False)
            )
            filtered = filtered[mask]
        if min_score > 0:
            filtered = filtered[filtered["watchlist_score"] >= min_score]
        if source_filter:
            filtered = filtered[filtered["fuente"].isin(source_filter)]

        # Tabs: tabla + mapa temporal + por país
        tab1, tab2, tab3 = st.tabs(["📋 Tabla", "📊 Por país", "📅 Línea de tiempo"])

        with tab1:
            # Tabla principal
            display_cols = ["fecha", "banda", "ciudad", "país", "festival", "watchlist_score", "fuente", "ticket_url"]
            display = filtered[display_cols].copy()
            display["watchlist_score"] = display["watchlist_score"].apply(
                lambda x: f"🔥 {x:.0f}" if x > 0 else "–"
            )
            display["ticket_url"] = display["ticket_url"].apply(
                lambda x: f"[🎟️ Tickets]({x})" if x else "–"
            )
            display.columns = ["Fecha", "Banda / Evento", "Ciudad", "País", "Festival", "Watchlist", "Fuente", "Tickets"]
            st.dataframe(
                display,
                use_container_width=True,
                height=500,
                column_config={
                    "Tickets": st.column_config.LinkColumn("Tickets"),
                    "Fecha": st.column_config.TextColumn("Fecha", width="small"),
                    "Watchlist": st.column_config.TextColumn("🔥", width="small"),
                },
            )
            st.caption(f"{len(filtered)} conciertos mostrados")

        with tab2:
            # Conciertos por país — barchart
            country_counts = filtered.groupby("país").size().reset_index(name="conciertos")
            country_counts = country_counts.sort_values("conciertos", ascending=True)
            fig = px.bar(
                country_counts,
                x="conciertos",
                y="país",
                orientation="h",
                color="conciertos",
                color_continuous_scale="Reds",
                title="Conciertos por país",
            )
            fig.update_layout(
                plot_bgcolor="#0e1117",
                paper_bgcolor="#0e1117",
                font_color="white",
                showlegend=False,
            )
            st.plotly_chart(fig, use_container_width=True)

            # Watchlist por país
            wl = filtered[filtered["watchlist_score"] > 0].groupby("país").size().reset_index(name="watchlist")
            if not wl.empty:
                fig2 = px.bar(
                    wl,
                    x="país",
                    y="watchlist",
                    color_discrete_sequence=["#e94560"],
                    title="🔥 Bandas de watchlist por país",
                )
                fig2.update_layout(
                    plot_bgcolor="#0e1117",
                    paper_bgcolor="#0e1117",
                    font_color="white",
                )
                st.plotly_chart(fig2, use_container_width=True)

        with tab3:
            # Timeline de conciertos
            filtered_sorted = filtered.copy()
            filtered_sorted["fecha_dt"] = pd.to_datetime(filtered_sorted["fecha"], errors="coerce")
            filtered_sorted = filtered_sorted.dropna(subset=["fecha_dt"])

            # Agrupar por semana y país
            filtered_sorted["semana"] = filtered_sorted["fecha_dt"].dt.to_period("M").astype(str)
            timeline = filtered_sorted.groupby(["semana", "país"]).size().reset_index(name="n")

            fig3 = px.bar(
                timeline,
                x="semana",
                y="n",
                color="país",
                title="Distribución temporal de conciertos",
                labels={"semana": "Mes", "n": "Conciertos", "país": "País"},
            )
            fig3.update_layout(
                plot_bgcolor="#0e1117",
                paper_bgcolor="#0e1117",
                font_color="white",
                xaxis_tickangle=-45,
            )
            st.plotly_chart(fig3, use_container_width=True)

# ──────────────────────────────────────────────────────────────────────────────
# PÁGINA: FESTIVALES
# ──────────────────────────────────────────────────────────────────────────────

elif page == "🎪 Festivales":
    st.header("🎪 Festivales monitoreados")

    festivals = load_festivals(days_ahead)
    if not festivals:
        st.info("No hay festivales en el rango seleccionado.")
    else:
        for fest in festivals:
            with st.expander(
                f"**{fest['festival']}** — {fest['ciudad']}, {fest['país']} · {fest['fecha']}",
                expanded=True,
            ):
                col_a, col_b = st.columns([2, 1])
                with col_a:
                    bandas = fest["bandas"]
                    if bandas:
                        st.markdown(f"**{len(bandas)} bandas confirmadas:**")
                        # Resaltar watchlist matches
                        wl_set = set(fest["watchlist_matches"])
                        chips = []
                        for b in sorted(set(bandas)):
                            if b in wl_set:
                                chips.append(f"🔥 **{b}**")
                            else:
                                chips.append(b)
                        st.markdown(" · ".join(chips))
                    else:
                        st.markdown("*Lineup pendiente de anunciar*")

                with col_b:
                    cc = fest["country_code"]
                    from src.shared.user_config import FLIGHT_ESTIMATE_USD, HOTEL_ESTIMATE_USD, BUY_WINDOW_FLIGHTS
                    flight = FLIGHT_ESTIMATE_USD.get(cc, (0, 0))
                    hotel = HOTEL_ESTIMATE_USD.get(cc, (0, 0))
                    st.markdown("**💰 Estimado desde Lima:**")
                    st.markdown(f"✈️ Vuelo: `${flight[0]}–${flight[1]}`")
                    st.markdown(f"🏨 Hotel 3n: `${hotel[0]*3}–${hotel[1]*3}`")
                    st.markdown(f"📦 Total: `${flight[0]+hotel[0]*3}–${flight[1]+hotel[1]*3}`")
                    st.markdown(f"🗓️ Comprar: *{BUY_WINDOW_FLIGHTS.get(cc, 'N/A')}*")
                    if fest.get("ticket_url"):
                        st.link_button("🎟️ Ver tickets", fest["ticket_url"])

# ──────────────────────────────────────────────────────────────────────────────
# PÁGINA: VUELOS & PRECIOS
# ──────────────────────────────────────────────────────────────────────────────

elif page == "✈️ Vuelos & Precios":
    st.header("✈️ Vuelos & Precios históricos")
    st.caption("Precios reales registrados por el Flight Agent en las últimas 90 días · Origen: Lima (LIM)")

    history = load_flight_history()

    if not history:
        st.info("Aún no hay suficiente historial de precios. El sistema necesita más runs diarios para construir el histórico (mínimo ~7 días).")
        st.markdown("""
        **¿Qué se registra?**
        - Cada vez que el orchestrator corre, el Flight Agent busca vuelos LIM → destino
        - Los precios se guardan en DynamoDB con timestamp
        - Con 60+ días de datos, el sistema puede detectar deals reales (precios bajo el percentil 25)
        """)
    else:
        hist_df = pd.DataFrame(history)

        # Selector de ruta
        rutas = sorted(hist_df["ruta"].unique())
        selected_ruta = st.selectbox("Selecciona ruta", rutas)
        ruta_df = hist_df[hist_df["ruta"] == selected_ruta]

        col_m1, col_m2, col_m3, col_m4 = st.columns(4)
        col_m1.metric("Precio mínimo", f"${ruta_df['precio_usd'].min():.0f}")
        col_m2.metric("Precio promedio", f"${ruta_df['precio_usd'].mean():.0f}")
        col_m3.metric("Precio máximo", f"${ruta_df['precio_usd'].max():.0f}")
        col_m4.metric("Registros", len(ruta_df))

        # Gráfico de tendencia
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=ruta_df["fecha_registro"],
            y=ruta_df["precio_usd"],
            mode="lines+markers",
            name="Precio USD",
            line=dict(color="#e94560", width=2),
            marker=dict(size=6),
        ))
        avg = ruta_df["precio_usd"].mean()
        fig.add_hline(
            y=avg,
            line_dash="dash",
            line_color="yellow",
            annotation_text=f"Promedio ${avg:.0f}",
            annotation_position="bottom right",
        )
        fig.update_layout(
            title=f"Historial de precios {selected_ruta}",
            xaxis_title="Fecha de registro",
            yaxis_title="Precio USD",
            plot_bgcolor="#0e1117",
            paper_bgcolor="#0e1117",
            font_color="white",
        )
        st.plotly_chart(fig, use_container_width=True)

        # Comparativa entre rutas
        st.subheader("📊 Comparativa de rutas")
        summary = hist_df.groupby("ruta").agg(
            min_usd=("precio_usd", "min"),
            avg_usd=("precio_usd", "mean"),
            max_usd=("precio_usd", "max"),
            registros=("precio_usd", "count"),
        ).reset_index().sort_values("avg_usd")

        fig2 = px.bar(
            summary,
            x="ruta",
            y=["min_usd", "avg_usd", "max_usd"],
            barmode="group",
            color_discrete_map={"min_usd": "#2ecc71", "avg_usd": "#f39c12", "max_usd": "#e74c3c"},
            labels={"value": "USD", "variable": "Precio"},
            title="Rango de precios por ruta (mín / prom / máx)",
        )
        fig2.update_layout(
            plot_bgcolor="#0e1117",
            paper_bgcolor="#0e1117",
            font_color="white",
            xaxis_tickangle=-30,
        )
        st.plotly_chart(fig2, use_container_width=True)

        with st.expander("📋 Ver todos los registros"):
            st.dataframe(
                hist_df[["fecha_registro", "ruta", "precio_usd", "aerolinea", "salida"]].sort_values("fecha_registro", ascending=False),
                use_container_width=True,
            )

# ──────────────────────────────────────────────────────────────────────────────
# PÁGINA: PRESUPUESTO
# ──────────────────────────────────────────────────────────────────────────────

elif page == "💰 Presupuesto":
    st.header("💰 Calculadora de presupuesto")
    st.caption("Estimados desde Lima (LIM). Los precios reales dependen de fechas y aerolínea.")

    budget_data = load_budget()
    budget_df = pd.DataFrame(budget_data)

    # Calculadora interactiva
    st.subheader("🧮 Calculadora de viaje")
    col_c1, col_c2, col_c3 = st.columns(3)
    with col_c1:
        dest_country = st.selectbox(
            "Destino",
            options=[r["country_code"] for r in budget_data],
            format_func=lambda x: COUNTRY_NAMES.get(x, x),
        )
    with col_c2:
        nights = st.number_input("Noches de hotel", min_value=1, max_value=14, value=3)
    with col_c3:
        ticket_price = st.number_input("Precio entrada (USD)", min_value=0, value=50, step=10)

    row = next((r for r in budget_data if r["country_code"] == dest_country), None)
    if row:
        f_min, f_max = row["vuelo_min_usd"], row["vuelo_max_usd"]
        h_min, h_max = row["hotel_noche_min_usd"] * nights, row["hotel_noche_max_usd"] * nights
        t_min = f_min + h_min + ticket_price
        t_max = f_max + h_max + ticket_price

        col_r1, col_r2, col_r3, col_r4 = st.columns(4)
        col_r1.metric("✈️ Vuelo", f"${f_min}–${f_max}")
        col_r2.metric(f"🏨 Hotel ({nights}n)", f"${h_min}–${h_max}")
        col_r3.metric("🎟️ Entrada", f"${ticket_price}")
        col_r4.metric("📦 TOTAL estimado", f"${t_min}–${t_max}")

        st.info(f"🗓️ Mejor momento para comprar el vuelo: **{row['comprar_vuelo']}**")

    st.divider()

    # Tabla comparativa todos los países
    st.subheader("📊 Comparativa completa desde Lima")

    col_n1, col_n2 = st.columns(2)
    with col_n1:
        nights_compare = st.slider("Noches para comparativa", 1, 7, 3)
    with col_n2:
        ticket_compare = st.number_input("Entrada estimada (USD)", min_value=0, value=60, step=10, key="compare_ticket")

    compare_data = []
    for r in budget_data:
        cc = r["country_code"]
        f_min, f_max = r["vuelo_min_usd"], r["vuelo_max_usd"]
        h_min = r["hotel_noche_min_usd"] * nights_compare
        h_max = r["hotel_noche_max_usd"] * nights_compare
        compare_data.append({
            "país": r["país"],
            "vuelo": f"${f_min}–${f_max}",
            "hotel": f"${h_min}–${h_max}",
            "total_min": f_min + h_min + ticket_compare,
            "total_max": f_max + h_max + ticket_compare,
            "comprar_vuelo": r["comprar_vuelo"],
        })

    compare_df = pd.DataFrame(compare_data).sort_values("total_min")
    compare_df["total"] = compare_df.apply(lambda r: f"${r['total_min']}–${r['total_max']}", axis=1)

    st.dataframe(
        compare_df[["país", "vuelo", "hotel", "total", "comprar_vuelo"]].rename(columns={
            "vuelo": "✈️ Vuelo (est.)",
            "hotel": "🏨 Hotel",
            "total": "📦 Total",
            "comprar_vuelo": "🗓️ Cuándo comprar",
        }),
        use_container_width=True,
        hide_index=True,
    )

    # Gráfico de barras comparativo
    fig = px.bar(
        compare_df,
        x="país",
        y=["total_min", "total_max"],
        barmode="group",
        color_discrete_map={"total_min": "#2ecc71", "total_max": "#e74c3c"},
        labels={"value": "USD", "variable": ""},
        title=f"Costo estimado total por país ({nights_compare}n hotel + entrada ~${ticket_compare})",
    )
    fig.update_layout(
        plot_bgcolor="#0e1117",
        paper_bgcolor="#0e1117",
        font_color="white",
        xaxis_tickangle=-30,
    )
    st.plotly_chart(fig, use_container_width=True)

# ──────────────────────────────────────────────────────────────────────────────
# PÁGINA: CONTROL
# ──────────────────────────────────────────────────────────────────────────────

elif page == "⚙️ Control":
    st.header("⚙️ Panel de control")

    # Trigger manual
    st.subheader("🔄 Ejecutar búsqueda manual")
    st.caption("Lanza el orchestrator para buscar nuevos conciertos y vuelos. Tarda ~5 minutos.")

    col_btn, col_info = st.columns([1, 3])
    with col_btn:
        if st.button("🚀 Ejecutar ahora", type="primary", use_container_width=True):
            with st.spinner("Lanzando orchestrator..."):
                result = trigger_orchestrator(async_mode=True)
            if result["success"]:
                st.success(result["message"])
            else:
                st.error(f"Error: {result['message']}")

    with col_info:
        st.info(
            "El orchestrator corre automáticamente todos los días. "
            "Usa este botón para forzar una ejecución inmediata y ver resultados frescos."
        )

    st.divider()

    # Últimas ejecuciones
    st.subheader("📋 Últimas ejecuciones del orchestrator")
    with st.spinner("Cargando historial..."):
        runs = get_last_runs(limit=10)

    if runs:
        for run in runs:
            st.markdown(f"- `{run['timestamp']}`")
    else:
        st.info("No se pudo cargar el historial de CloudWatch.")

    st.divider()

    # Cache control
    st.subheader("🗑️ Cache")
    if st.button("Limpiar cache del dashboard"):
        st.cache_data.clear()
        st.success("Cache limpiado. Los datos se recargarán en la próxima acción.")

    st.divider()

    # Info del sistema
    st.subheader("ℹ️ Estado del sistema")
    col_s1, col_s2 = st.columns(2)
    with col_s1:
        st.markdown(f"""
        **DynamoDB Table:** `{os.environ.get('DYNAMODB_TABLE_CONCERTS', 'metal-travel-tracker-prod-concerts')}`
        **AWS Region:** `{os.environ.get('AWS_REGION', 'us-east-1')}`
        **Orchestrator:** `metal-travel-tracker-prod-orchestrator`
        """)
    with col_s2:
        stats2 = load_stats()
        st.markdown(f"""
        **Países monitoreados:** {stats2['countries_with_data']}/11
        **Total conciertos en DB:** {stats2['total']}
        **Watchlist matches:** {stats2['watchlist_hits']}
        """)

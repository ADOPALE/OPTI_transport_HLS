import streamlit as st
import pandas as pd
import plotly.express as px

def show_flux_control_charts():
    """Exploite st.session_state['data']['m_flux'] sans relire Excel"""
    
    if "data" not in st.session_state or "m_flux" not in st.session_state["data"]:
        st.warning("⚠️ Données de flux non détectées dans la session.")
        return

    df_flux = st.session_state["data"]["m_flux"].copy()

    # Détection dynamique des colonnes (insensible à la casse)
    cols = {
        "jour": next((c for c in df_flux.columns if "jour" in c.lower()), None),
        "sens": next((c for c in df_flux.columns if "sens" in c.lower()), None),
        "vol": next((c for c in df_flux.columns if "vol" in c.lower() or "quant" in c.lower()), None),
        "fonc": next((c for c in df_flux.columns if "fonc" in c.lower() or "pôle" in c.lower()), None)
    }

    if not all(cols.values()):
        st.error(f"Colonnes introuvables. Vérifiez l'onglet 'M flux'.")
        return

    # Tri chronologique
    jours = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    df_flux[cols["jour"]] = pd.Categorical(df_flux[cols["jour"]], categories=jours, ordered=True)

    st.divider()
    st.subheader("📊 Graphiques de Contrôle des Flux")

    # --- GRAPHIQUE GLOBAL ---
    st.markdown("#### Volume Global par Jour et Sens")
    df_gb = df_flux.groupby([cols["jour"], cols["sens"]])[cols["vol"]].sum().reset_index()
    fig_gb = px.bar(df_gb, x=cols["jour"], y=cols["vol"], color=cols["sens"],
                    barmode="group", template="plotly_dark",
                    color_discrete_map={"Aller": "#00CC96", "Retour": "#EF553B"})
    st.plotly_chart(fig_gb, use_container_width=True)

    # --- DETAIL PAR FONCTION ---
    with st.expander("🔍 Détail par Fonction Support", expanded=False):
        for fonc in df_flux[cols["fonc"]].unique():
            st.write(f"**Flux : {fonc}**")
            df_sub = df_flux[df_flux[cols["fonc"]] == fonc].groupby([cols["jour"], cols["sens"]])[cols["vol"]].sum().reset_index()
            fig_sub = px.bar(df_sub, x=cols["jour"], y=cols["vol"], color=cols["sens"],
                             barmode="group", height=300, template="plotly_dark")
            st.plotly_chart(fig_sub, use_container_width=True)

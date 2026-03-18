import streamlit as st
import pandas as pd
import plotly.express as px

def show_flux_control_charts():
    """Exploite st.session_state['data']['m_flux'] avec détection précise des colonnes"""
    
    if "data" not in st.session_state or "m_flux" not in st.session_state["data"]:
        st.warning("⚠️ Données de flux non détectées. Veuillez importer le fichier.")
        return

    df_flux = st.session_state["data"]["m_flux"].copy()

    # MAPPING PRÉCIS basé sur votre fichier
    # On cherche des correspondances souples pour éviter les erreurs d'espaces
    col_jour = next((c for c in df_flux.columns if "jour" in c.lower() and "quant" not in c.lower()), None)
    col_sens = next((c for c in df_flux.columns if "sens" in c.lower()), None)
    col_vol = next((c for c in df_flux.columns if "quant" in c.lower() or "roll" in c.lower()), None)
    col_fonc = next((c for c in df_flux.columns if "flux" in c.lower()), None)

    # Vérification de sécurité
    if not all([col_jour, col_sens, col_vol, col_fonc]):
        st.error("❌ Structure de l'onglet 'M flux' non reconnue.")
        st.info(f"Colonnes trouvées : {list(df_flux.columns)}")
        return

    # Nettoyage des données
    df_flux[col_vol] = pd.to_numeric(df_flux[col_vol], errors='coerce').fillna(0)
    
    # Tri des jours
    jours_ordre = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    df_flux[col_jour] = pd.Categorical(df_flux[col_jour].str.capitalize(), categories=jours_ordre, ordered=True)

    st.divider()
    st.subheader("📊 Contrôle de cohérence des flux")

    # --- GRAPHIQUE GLOBAL ---
    st.markdown("#### Volume total (Rolls) par jour et par sens")
    df_gb = df_flux.groupby([col_jour, col_sens], observed=False)[col_vol].sum().reset_index()
    
    fig_gb = px.bar(
        df_gb, x=col_jour, y=col_vol, color=col_sens,
        barmode="group", template="plotly_dark",
        color_discrete_map={"Aller": "#00CC96", "Retour": "#EF553B"},
        labels={col_vol: "Nombre de Rolls", col_jour: "Jour"}
    )
    st.plotly_chart(fig_gb, use_container_width=True)

    # --- DETAIL PAR TYPE DE FLUX ---
    with st.expander("🔍 Détail par Code Flux", expanded=False):
        fonctions = df_flux[col_fonc].unique()
        for f in fonctions:
            df_f = df_flux[df_flux[col_fonc] == f].groupby([col_jour, col_sens], observed=False)[col_vol].sum().reset_index()
            if df_f[col_vol].sum() > 0:
                fig_f = px.bar(
                    df_f, x=col_jour, y=col_vol, color=col_sens, 
                    barmode="group", template="plotly_dark", title=f"Flux : {f}",
                    color_discrete_map={"Aller": "#00CC96", "Retour": "#EF553B"}
                )
                st.plotly_chart(fig_f, use_container_width=True)

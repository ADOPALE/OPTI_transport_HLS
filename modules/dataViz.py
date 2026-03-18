import streamlit as st
import pandas as pd
import plotly.express as px

def show_flux_control_charts():
    """Version durcie : nettoyage des espaces et conversion numérique forcée"""
    
    if "data" not in st.session_state or "m_flux" not in st.session_state["data"]:
        st.warning("⚠️ Données de flux non détectées dans st.session_state['data']")
        return

    # Travail sur une copie propre
    df = st.session_state["data"]["m_flux"].copy()

    # --- ÉTAPE 1 : NETTOYAGE DES COLONNES ---
    # On supprime les espaces invisibles dans les noms de colonnes
    df.columns = df.columns.str.strip()
    
    col_fonc = "Fonction Support associée"
    col_sens = "Aller / Retour"
    
    jours_cols = [
        "Quantité Lundi", "Quantité Mardi", "Quantité Mercredi", 
        "Quantité Jeudi", "Quantité Vendredi", "Quantité Samedi", "Quantité Dimanche"
    ]

    # Vérification présence colonnes
    missing = [c for c in [col_fonc, col_sens] if c not in df.columns]
    if missing:
        st.error(f"Colonnes critiques absentes : {missing}")
        st.info(f"Colonnes disponibles : {list(df.columns)}")
        return

    # --- ÉTAPE 2 : NETTOYAGE DES DONNÉES ---
    # Nettoyage de la colonne Sens (enlever espaces comme "Aller ")
    df[col_sens] = df[col_sens].astype(str).str.strip()

    # Conversion forcée des colonnes jours en nombres (remplace le texte par 0)
    for j_col in jours_cols:
        if j_col in df.columns:
            df[j_col] = pd.to_numeric(df[j_col], errors='coerce').fillna(0)

    # --- ÉTAPE 3 : TRANSFORMATION ---
    df_long = df.melt(
        id_vars=[col_fonc, col_sens],
        value_vars=[c for c in jours_cols if c in df.columns],
        var_name="Jour_Full",
        value_name="Valeur"
    )

    # Simplification du nom du jour (ex: "Quantité Lundi" -> "Lundi")
    df_long["Jour"] = df_long["Jour_Full"].str.replace("Quantité ", "").str.strip()

    # Tri chronologique
    ordre = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    df_long["Jour"] = pd.Categorical(df_long["Jour"], categories=ordre, ordered=True)

    # --- ÉTAPE 4 : AFFICHAGE ---
    st.divider()
    st.subheader("📊 Contrôle visuel des flux")

    if df_long["Valeur"].sum() == 0:
        st.error("❌ La somme des quantités est égale à 0. Vérifiez le format des nombres dans Excel.")
        return

    # Graphique Global
    df_gb = df_long.groupby(["Jour", col_sens], observed=False)["Valeur"].sum().reset_index()
    
    fig_global = px.bar(
        df_gb, x="Jour", y="Valeur", color=col_sens,
        barmode="group",
        template="plotly_dark",
        color_discrete_map={"Aller": "#00CC96", "Retour": "#EF553B"},
        title="Volume Hebdomadaire Global"
    )
    st.plotly_chart(fig_global, use_container_width=True)

    # Détail par Fonction
    with st.expander("🔍 Détail par Fonction Support", expanded=False):
        for f in df_long[col_fonc].unique():
            df_sub = df_long[df_long[col_fonc] == f].groupby(["Jour", col_sens], observed=False)["Valeur"].sum().reset_index()
            if df_sub["Valeur"].sum() > 0:
                fig_f = px.bar(
                    df_sub, x="Jour", y="Valeur", color=col_sens,
                    barmode="group", template="plotly_dark", 
                    title=f"Flux : {f}",
                    color_discrete_map={"Aller": "#00CC96", "Retour": "#EF553B"}
                )
                st.plotly_chart(fig_f, use_container_width=True)

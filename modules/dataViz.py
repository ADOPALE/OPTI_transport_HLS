import streamlit as st
import pandas as pd
import plotly.express as px

def show_flux_control_charts():
    """Version corrigée : Somme les colonnes 'Quantité Jour' par 'Fonction Support associée'"""
    
    if "data" not in st.session_state or "m_flux" not in st.session_state["data"]:
        st.warning("⚠️ Données de flux non détectées.")
        return

    df_flux = st.session_state["data"]["m_flux"].copy()

    # Définition des colonnes sources selon tes spécifications
    col_fonc = "Fonction Support associée"
    col_sens = "Aller / Retour"
    
    # Mapping des colonnes jours vers les noms d'affichage
    mapping_jours = {
        "Quantité Lundi": "Lundi",
        "Quantité Mardi": "Mardi",
        "Quantité Mercredi": "Mercredi",
        "Quantité Jeudi": "Jeudi",
        "Quantité Vendredi": "Vendredi",
        "Quantité Samedi": "Samedi",
        "Quantité Dimanche": "Dimanche"
    }

    # Vérification de la présence des colonnes minimales
    cols_presentes = df_flux.columns.tolist()
    if col_fonc not in cols_presentes or col_sens not in cols_presentes:
        st.error(f"Colonnes '{col_fonc}' ou '{col_sens}' introuvables.")
        return

    # 1. Transformation du format Large (colonnes jours) vers format Long (une ligne par jour)
    # Cela permet de construire l'histogramme facilement avec Plotly
    df_long = df_flux.melt(
        id_vars=[col_fonc, col_sens],
        value_vars=[j for j in mapping_jours.keys() if j in cols_presentes],
        var_name="Jour_Brut",
        value_name="Quantite"
    )

    # Nettoyage : renomer les jours et convertir les quantités
    df_long["Jour"] = df_long["Jour_Brut"].map(mapping_jours)
    df_long["Quantite"] = pd.to_numeric(df_long["Quantite"], errors='coerce').fillna(0)

    # Tri chronologique
    jours_ordre = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    df_long["Jour"] = pd.Categorical(df_long["Jour"], categories=jours_ordre, ordered=True)

    st.divider()
    st.subheader("📊 Contrôle de cohérence des flux")

    # --- GRAPHIQUE 1 : GLOBAL ---
    st.markdown("#### Volume Total Hebdomadaire (Aller / Retour)")
    df_gb = df_long.groupby(["Jour", col_sens], observed=False)["Quantite"].sum().reset_index()
    
    fig_gb = px.bar(
        df_gb, x="Jour", y="Quantite", color=col_sens,
        barmode="group",
        template="plotly_dark",
        color_discrete_map={"Aller": "#00CC96", "Retour": "#EF553B", "Aller ": "#00CC96", "Retour ": "#EF553B"},
        labels={"Quantite": "Total Quantité", "Jour": ""}
    )
    st.plotly_chart(fig_gb, use_container_width=True)

    # --- GRAPHIQUE 2 : DÉTAIL PAR FONCTION SUPPORT ---
    with st.expander("🔍 Détail par Fonction Support associée", expanded=True):
        fonctions = df_long[col_fonc].unique()
        for fonc in fonctions:
            df_sub = df_long[df_long[col_fonc] == fonc].groupby(["Jour", col_sens], observed=False)["Quantite"].sum().reset_index()
            
            if df_sub["Quantite"].sum() > 0:
                fig_sub = px.bar(
                    df_sub, x="Jour", y="Quantite", color=col_sens,
                    barmode="group",
                    title=f"Support : {fonc}",
                    template="plotly_dark",
                    color_discrete_map={"Aller": "#00CC96", "Retour": "#EF553B", "Aller ": "#00CC96", "Retour ": "#EF553B"},
                    height=300
                )
                st.plotly_chart(fig_sub, use_container_width=True)

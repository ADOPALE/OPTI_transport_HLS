import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

def show_flux_control_charts():
    if "data" not in st.session_state or "m_flux" not in st.session_state["data"]:
        return

    df = st.session_state["data"]["m_flux"].copy()
    df.columns = df.columns.str.strip()
    
    col_fonc = "Fonction Support associée"
    col_sens = "Aller / Retour"
    jours_cols = ["Quantité Lundi", "Quantité Mardi", "Quantité Mercredi", "Quantité Jeudi", "Quantité Vendredi", "Quantité Samedi", "Quantité Dimanche"]

    # Nettoyage
    df[col_sens] = df[col_sens].astype(str).str.strip()
    for j in jours_cols:
        if j in df.columns:
            df[j] = pd.to_numeric(df[j], errors='coerce').fillna(0)

    # Passage en format long
    df_long = df.melt(id_vars=[col_fonc, col_sens], value_vars=[c for c in jours_cols if c in df.columns],
                      var_name="Jour_Full", value_name="Valeur")
    df_long["Jour"] = df_long["Jour_Full"].str.replace("Quantité ", "").str.strip()
    ordre = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    df_long["Jour"] = pd.Categorical(df_long["Jour"], categories=ordre, ordered=True)

    st.divider()
    st.subheader("📊 Répartition Globale des Flux")

    # --- GÉNÉRATION DE LA PALETTE DE COULEURS ---
    # Couleurs de base (teintes logo Chu/Adopale : Bleu, Orange, Vert, Gris-Rouge)
    base_colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2"]
    fonctions = sorted(df_long[col_fonc].unique())
    color_map = {}
    
    for i, func in enumerate(fonctions):
        color = base_colors[i % len(base_colors)]
        # On crée une clé unique "Fonction + Sens"
        color_map[f"{func} - Aller"] = color 
        # On assombrit la couleur pour le retour (via opacité ou code hex si fixe)
        color_map[f"{func} - Retour"] = color # Plotly gérera la luminosité via marker_pattern ou opacité

    # --- CONSTRUCTION DU GRAPHIQUE GLOBAL (STACKED) ---
    # Pour l'affichage par fonction support comme l'image, on regroupe
    df_gb = df_long.groupby(["Jour", col_fonc, col_sens], observed=False)["Valeur"].sum().reset_index()
    
    fig = go.Figure()

    for func in fonctions:
        for sens in ["Aller", "Retour"]:
            subset = df_gb[(df_gb[col_fonc] == func) & (df_gb[col_sens] == sens)]
            
            # Définition de la couleur : Claire pour Aller, Sombre pour Retour
            base_c = base_colors[fonctions.index(func) % len(base_colors)]
            final_c = base_c if sens == "Aller" else f"rgba{tuple(list(int(base_c.lstrip('#')[i:i+2], 16) for i in (0, 2, 4)) + [0.5])}" 
            # Note : Le retour est ici défini avec 50% d'opacité de la même couleur pour paraître plus sombre/terne
            
            fig.add_trace(go.Bar(
                name=f"{func} ({sens})",
                x=subset["Jour"],
                y=subset["Valeur"],
                marker_color=final_c,
                # On groupe par jour, mais on empile les fonctions
                offsetgroup=sens, 
                text=subset["Valeur"].apply(lambda x: int(x) if x > 0 else ""),
                textposition='auto',
            ))

    fig.update_layout(
        barmode='stack',
        template="plotly_dark",
        title="Volume par Jour (Gauche: Aller | Droite: Retour)",
        xaxis_title="",
        yaxis_title="Nombre de Rolls",
        legend_title="Fonctions Support",
        uniformtext_minsize=8, 
        uniformtext_mode='hide'
    )
    
    st.plotly_chart(fig, use_container_width=True)

    # --- DÉTAIL PAR FONCTION ---
    with st.expander("🔍 Voir le détail par service", expanded=False):
        for f in fonctions:
            df_sub = df_long[df_long[col_fonc] == f].groupby(["Jour", col_sens], observed=False)["Valeur"].sum().reset_index()
            if df_sub["Valeur"].sum() > 0:
                fig_sub = px.bar(
                    df_sub, x="Jour", y="Valeur", color=col_sens,
                    barmode="group", template="plotly_dark", title=f"Détail : {f}",
                    color_discrete_map={"Aller": "#3498db", "Retour": "#21618c"}, # Exemple bleu clair / bleu foncé
                    text_auto=True
                )
                st.plotly_chart(fig_sub, use_container_width=True)

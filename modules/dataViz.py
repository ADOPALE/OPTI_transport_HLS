import streamlit as st
import pandas as pd
import plotly.graph_objects as go

def get_contrast_color(hex_color):
    """Détermine si le texte doit être noir ou blanc selon la luminosité du fond"""
    hex_color = hex_color.lstrip('#')
    if len(hex_color) == 3:
        hex_color = ''.join([c*2 for c in hex_color])
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    brightness = (r * 299 + g * 587 + b * 114) / 1000
    return "white" if brightness < 128 else "black"

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

    # Palette de couleurs cohérente
    palette_hex = ["#005596", "#E67E22", "#27AE60", "#8E44AD", "#C0392B", "#2C3E50", "#F1C40F"]
    fonctions = sorted(df_long[col_fonc].unique())
    color_map_base = {f: palette_hex[i % len(palette_hex)] for i, f in enumerate(fonctions)}

    st.divider()
    st.subheader("📊 Répartition Globale des Flux")

    df_gb = df_long.groupby(["Jour", col_fonc, col_sens], observed=False)["Valeur"].sum().reset_index()
    
    fig = go.Figure()

    # 1. Ajout des barres empilées par fonction
    for func in fonctions:
        base_color = color_map_base[func]
        dark_color = f"rgba({int(base_color[1:3], 16)}, {int(base_color[3:5], 16)}, {int(base_color[5:7], 16)}, 0.4)"
        text_color_aller = get_contrast_color(base_color)

        for sens in ["Aller", "Retour"]:
            subset = df_gb[(df_gb[col_fonc] == func) & (df_gb[col_sens] == sens)]
            color = base_color if sens == "Aller" else dark_color
            
            fig.add_trace(go.Bar(
                name=f"{func} ({sens})",
                x=subset["Jour"],
                y=subset["Valeur"],
                marker_color=color,
                offsetgroup=sens,
                text=subset["Valeur"].apply(lambda x: int(x) if x > 0 else ""),
                textposition='inside',
                insidetextanchor='middle',
                textfont=dict(color=text_color_aller if sens == "Aller" else "white"),
                hovertemplate="<b>" + func + "</b><br>Sens: " + sens + "<br>Quantité: %{y}<extra></extra>"
            ))

    # 2. Ajout des TOTAUX au-dessus des piles
    # On calcule la somme totale par Jour et par Sens (indépendamment de la fonction)
    df_totals = df_long.groupby(["Jour", col_sens], observed=False)["Valeur"].sum().reset_index()

    for sens in ["Aller", "Retour"]:
        totals_subset = df_totals[df_totals[col_sens] == sens]
        
        fig.add_trace(go.Bar(
            x=totals_subset["Jour"],
            y=totals_subset["Valeur"],
            offsetgroup=sens,
            text=totals_subset["Valeur"].apply(lambda x: f"<b>{int(x)}</b>" if x > 0 else ""),
            textposition='outside',
            marker_color='rgba(0,0,0,0)', # Invisible
            showlegend=False,
            hoverinfo='skip'
        ))

    fig.update_layout(
        barmode='stack',
        template="plotly_dark",
        height=600,
        yaxis_title="Nombre de Rolls",
        legend_title="Fonctions Support",
        xaxis=dict(title=""),
        margin=dict(t=50) # Espace pour les étiquettes du haut
    )
    
    st.plotly_chart(fig, use_container_width=True)

    # --- DÉTAIL PAR FONCTION SUPPORT ---
    st.markdown("### 🔍 Détail par FONCTION SUPPORT")
    for f in fonctions:
        df_sub = df_long[df_long[col_fonc] == f].groupby(["Jour", col_sens], observed=False)["Valeur"].sum().reset_index()
        if df_sub["Valeur"].sum() > 0:
            base_color = color_map_base[f]
            dark_color = f"rgba({int(base_color[1:3], 16)}, {int(base_color[3:5], 16)}, {int(base_color[5:7], 16)}, 0.4)"
            
            with st.expander(f"Flux : {f}", expanded=False):
                fig_sub = go.Figure()
                for sens in ["Aller", "Retour"]:
                    sub_sens = df_sub[df_sub[col_sens] == sens]
                    color = base_color if sens == "Aller" else dark_color
                    fig_sub.add_trace(go.Bar(
                        name=sens, x=sub_sens["Jour"], y=sub_sens["Valeur"],
                        marker_color=color,
                        text=sub_sens["Valeur"].apply(lambda x: int(x) if x > 0 else ""),
                        textposition='auto',
                        textfont=dict(color="white")
                    ))
                fig_sub.update_layout(template="plotly_dark", barmode="group", height=350)
                st.plotly_chart(fig_sub, use_container_width=True)

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

def get_contrast_color(hex_color):
    """Calcule si le texte doit être blanc ou noir selon le fond"""
    if hex_color.startswith('rgba'):
        # Pour les couleurs transparentes (Retour), on considère le fond noir du mode dark
        return "white"
    hex_color = hex_color.lstrip('#')
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

    # Format long
    df_long = df.melt(id_vars=[col_fonc, col_sens], value_vars=[c for c in jours_cols if c in df.columns],
                      var_name="Jour_Full", value_name="Valeur")
    df_long["Jour"] = df_long["Jour_Full"].str.replace("Quantité ", "").str.strip()
    ordre = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    df_long["Jour"] = pd.Categorical(df_long["Jour"], categories=ordre, ordered=True)

    # Palette
    palette_hex = ["#005596", "#E67E22", "#27AE60", "#8E44AD", "#C0392B", "#2C3E50", "#F1C40F"]
    fonctions = sorted(df_long[col_fonc].unique())
    color_map_base = {f: palette_hex[i % len(palette_hex)] for i, f in enumerate(fonctions)}

    st.divider()
    st.subheader("📊 Répartition Globale (Barre GAUCHE = Aller | Barre DROITE = Retour)")

    # --- DONNÉES TABLEAU ---
    df_totals = df_long.groupby(["Jour", col_sens], observed=False)["Valeur"].sum().reset_index()
    df_pivot = df_totals.pivot(index=col_sens, columns="Jour", values="Valeur").fillna(0)
    df_pivot.loc["TOTAL (A+R)"] = df_pivot.sum()
    
    # --- SUBPLOT ---
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08, # Augmenté pour éviter la superposition
        specs=[[{"type": "bar"}], [{"type": "table"}]]
    )

    df_gb = df_long.groupby(["Jour", col_fonc, col_sens], observed=False)["Valeur"].sum().reset_index()
    
    for func in fonctions:
        base_color = color_map_base[func]
        dark_color = f"rgba({int(base_color[1:3], 16)}, {int(base_color[3:5], 16)}, {int(base_color[5:7], 16)}, 0.4)"
        
        for sens in ["Aller", "Retour"]:
            subset = df_gb[(df_gb[col_fonc] == func) & (df_gb[col_sens] == sens)]
            color = base_color if sens == "Aller" else dark_color
            
            fig.add_trace(go.Bar(
                name=f"{func} ({sens})",
                x=subset["Jour"], y=subset["Valeur"],
                marker_color=color,
                offsetgroup=sens,
                text=subset["Valeur"].apply(lambda x: int(x) if x > 0 else ""),
                textposition='inside',
                insidetextanchor='middle',
                textfont=dict(color=get_contrast_color(color), size=10),
                legendgroup=func
            ), row=1, col=1)

    # Tableau
    header_list = ["<b>Volumes / Jours</b>"] + [f"<b>{j}</b>" for j in ordre]
    rows = [[f"<b>{lbl}</b>"] + [int(v) for v in df_pivot.loc[lbl].values] for lbl in ["Aller", "Retour", "TOTAL (A+R)"]]

    fig.add_trace(go.Table(
        header=dict(values=header_list, fill_color='#1f1f1f', align='center', font=dict(color='white', size=11)),
        cells=dict(
            values=list(zip(*rows)),
            fill_color=[['#262626', '#1a1a1a', 'black']*8],
            align='center', font=dict(color='white', size=10), height=22
        )
    ), row=2, col=1)

    fig.update_layout(
        #title="Répartition par Fonction (Barre GAUCHE = Aller | Barre DROITE = Retour)",
        barmode='stack', template="plotly_dark", height=500, # Hauteur augmentée
        margin=dict(t=0, b=0, l=10, r=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, font=dict(size=10))
    )
    
    st.plotly_chart(fig, use_container_width=True)

    # --- DÉTAIL PAR FONCTION SUPPORT (AVEC CONTRASTE) ---
    st.markdown("### 🔍 Détail par Fonction Support")
    for f in fonctions:
        df_sub = df_long[df_long[col_fonc] == f].groupby(["Jour", col_sens], observed=False)["Valeur"].sum().reset_index()
        if df_sub["Valeur"].sum() > 0:
            with st.expander(f"Analyse : {f}", expanded=False):
                base_color = color_map_base[f]
                dark_color = f"rgba({int(base_color[1:3], 16)}, {int(base_color[3:5], 16)}, {int(base_color[5:7], 16)}, 0.4)"
                
                fig_sub = go.Figure()
                for sens in ["Aller", "Retour"]:
                    sub_s = df_sub[df_sub[col_sens] == sens]
                    color = base_color if sens == "Aller" else dark_color
                    
                    fig_sub.add_trace(go.Bar(
                        name=sens, x=sub_s["Jour"], y=sub_s["Valeur"],
                        marker_color=color,
                        text=sub_s["Valeur"].apply(lambda x: int(x) if x > 0 else ""),
                        textposition='auto',
                        textfont=dict(color=get_contrast_color(color)) # Correction contraste ici
                    ))
                fig_sub.update_layout(template="plotly_dark", barmode="group", height=300)
                st.plotly_chart(fig_sub, use_container_width=True)

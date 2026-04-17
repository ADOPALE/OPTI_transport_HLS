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

    # --- MODIFICATION ICI POUR ÉVITER LE TYPEERROR ---
    # On filtre les valeurs nulles et on force en string avant le tri
    unique_foncs = df_long[col_fonc].dropna().unique()
    fonctions = sorted([str(f) for f in unique_foncs])
    # ------------------------------------------------

    # Palette
    palette_hex = ["#005596", "#E67E22", "#27AE60", "#8E44AD", "#C0392B", "#2C3E50", "#F1C40F"]
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
        vertical_spacing=0.08, 
        specs=[[{"type": "bar"}], [{"type": "table"}]]
    )

    df_gb = df_long.groupby(["Jour", col_fonc, col_sens], observed=False)["Valeur"].sum().reset_index()
    
    for func in fonctions:
        base_color = color_map_base.get(func, "#808080")
        # Calcul sécurisé de la couleur sombre (transparence)
        try:
            r, g, b = int(base_color[1:3], 16), int(base_color[3:5], 16), int(base_color[5:7], 16)
            dark_color = f"rgba({r}, {g}, {b}, 0.4)"
        except:
            dark_color = "rgba(128, 128, 128, 0.4)"
        
        for sens in ["Aller", "Retour"]:
            subset = df_gb[(df_gb[col_fonc].astype(str) == func) & (df_gb[col_sens] == sens)]
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
        barmode='stack', template="plotly_dark", height=500,
        margin=dict(t=0, b=0, l=10, r=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, font=dict(size=10))
    )
    
    st.plotly_chart(fig, use_container_width=True)

   # --- DÉTAIL PAR FONCTION SUPPORT ---
    st.markdown("### 🔍 Détail par Fonction Support")
    
    # On identifie les fonctions uniques valides
    fonctions_liste = [f for f in df[col_fonc].unique() if str(f).lower() != 'nan' and str(f).strip() != '']

    for f in fonctions_liste:
        # 1. Filtrage manuel pour éviter les erreurs d'index Pandas
        df_f = df_long[df_long[col_fonc].astype(str) == str(f)].copy()
        
        if not df_f.empty:
            # 2. Calcul des sommes par dictionnaire (Méthode 100% sûre anti-IndexError)
            jours_standards = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
            stats_finaux = {j: {"Aller": 0, "Retour": 0} for j in jours_standards}
            
            for _, row in df_f.iterrows():
                j_nom = str(row["Jour"])
                s_nom = str(row[col_sens])
                val = pd.to_numeric(str(row["Valeur"]).replace(',', '.'), errors='coerce')
                if j_nom in stats_finaux and s_nom in ["Aller", "Retour"] and pd.notnull(val):
                    stats_finaux[j_nom][s_nom] += val

            total_f = sum(stats_finaux[j]["Aller"] + stats_finaux[j]["Retour"] for j in jours_standards)

            if total_f > 0:
                with st.expander(f"Analyse : {f}", expanded=False):
                    base_color = color_map_base.get(f, "#808080")
                    
                    # Couleur pour le Retour
                    try:
                        rgb = base_color.lstrip('#')
                        r, g, b = tuple(int(rgb[i:i+2], 16) for i in (0, 2, 4))
                        dark_color = f"rgba({r}, {g}, {b}, 0.4)"
                    except:
                        dark_color = "rgba(128, 128, 128, 0.4)"
                    
                    fig_sub = go.Figure()
                    
                    for sens in ["Aller", "Retour"]:
                        y_vals = [stats_finaux[j][sens] for j in jours_standards]
                        color = base_color if sens == "Aller" else dark_color
                        
                        fig_sub.add_trace(go.Bar(
                            name=sens,
                            x=jours_standards,
                            y=y_vals,
                            marker_color=color,
                            offsetgroup=sens,
                            text=[int(v) if v > 0 else "" for v in y_vals],
                            textposition='outside',
                            textfont=dict(color="white")
                        ))

                    fig_sub.update_layout(
                        barmode='group',
                        xaxis_title=None,
                        yaxis_title="Nb contenants",
                        plot_bgcolor='rgba(0,0,0,0)',
                        paper_bgcolor='rgba(0,0,0,0)',
                        font=dict(color="white"),
                        height=350,
                        margin=dict(l=20, r=20, t=40, b=20),
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
                    )
                    st.plotly_chart(fig_sub, use_container_width=True)

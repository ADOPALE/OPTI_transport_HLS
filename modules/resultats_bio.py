import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import numpy as np
import folium
from streamlit_folium import st_folium

def calculate_kpis(flotte, config_rh, df_dist):
    """Calcule les indicateurs de performance."""
    total_vehicules = len(flotte)
    all_postes = [p for v in flotte.values() for p in v]
    total_chauffeurs = len(all_postes)
    
    total_tournees = sum(len(p) for p in all_postes)
    duree_utile_chauffeur = config_rh['amplitude'] - config_rh['pause']
    
    total_km = 0
    durees_travail = []
    
    for v_id, postes in flotte.items():
        for vacation in postes:
            duree_v = 0
            for tournee in vacation:
                duree_v += (tournee[-1]['heure'] - tournee[0]['heure'])
                # Calcul KM réel via la matrice
                for i in range(len(tournee)-1):
                    s1, s2 = tournee[i]['site'], tournee[i+1]['site']
                    try:
                        total_km += df_dist.loc[s1, s2]
                    except:
                        total_km += 5 # Valeur par défaut si site manquant
            durees_travail.append(duree_v)

    occupancy = (np.mean(durees_travail) / duree_utile_chauffeur * 100) if durees_travail else 0
    
    return {
        "v_total": total_vehicules,
        "c_total": total_chauffeurs,
        "tx_occ": occupancy,
        "t_total": total_tournees,
        "t_moy": total_tournees / total_chauffeurs if total_chauffeurs > 0 else 0,
        "km_total": total_km,
        "km_moy": total_km / total_chauffeurs if total_chauffeurs > 0 else 0
    }

def render_fleet_gantt(flotte, v_highlight=None):
    """Histogramme horizontal des occupations."""
    data = []
    for v_id, postes in flotte.items():
        if v_highlight and v_id != v_highlight: continue
        for p_idx, vacation in enumerate(postes):
            for t_idx, tournee in enumerate(vacation):
                data.append({
                    "Véhicule": v_id,
                    "Début": tournee[0]['heure'],
                    "Fin": tournee[-1]['heure'],
                    "Type": "Occupé",
                    "Label": f"Chauffeur {p_idx+1} - T{t_idx+1}"
                })
    
    df = pd.DataFrame(data)
    # Conversion minutes -> format heure pour l'axe
    fig = px.timeline(df, x_start="Début", x_end="Fin", y="Véhicule", color_discrete_sequence=["#00CC96"])
    
    # Simulation d'axe temporel (Plotly timeline attend des dates, on ruse avec linear)
    fig.update_layout(xaxis=dict(type='linear', range=[420, 1200], title="Minutes de la journée (7h-20h)"),
                      yaxis=dict(autorange="reversed"), template="plotly_dark")
    st.plotly_chart(fig, use_container_width=True)

def render_site_passages(flotte):
    """Nuage de points des passages par site."""
    data = []
    for v_id, postes in flotte.items():
        for vac in postes:
            for trne in vac:
                for step in trne:
                    if step['site'] != "HLS":
                        data.append({"Site": step['site'], "Heure": step['heure'], "Véhicule": v_id})
    
    df = pd.DataFrame(data)
    fig = px.scatter(df, x="Heure", y="Site", color="Véhicule", symbol="Véhicule")
    fig.update_layout(xaxis=dict(type='linear', range=[420, 1200]), template="plotly_dark")
    st.plotly_chart(fig, use_container_width=True)

def render_tournee_map(tournee_steps, df_sites):
    """Carte Folium d'une tournée spécifique."""
    # On récupère les coordonnées depuis l'onglet param_sites (chargé via Import.py)
    m = folium.Map(location=[47.21, -1.55], zoom_start=11)
    points = []
    for step in tournee_steps:
        site = step['site']
        row = df_sites[df_sites['site'] == site]
        if not row.empty:
            loc = [row.iloc[0]['latitude'], row.iloc[0]['longitude']]
            points.append(loc)
            folium.Marker(loc, popup=f"{site} ({int(step['heure']//60)}h{int(step['heure']%60):02d})").add_to(m)
    
    if len(points) > 1:
        folium.PolyLine(points, color="blue", weight=3).add_to(m)
    st_folium(m, width=700, height=400)

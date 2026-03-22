import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import timedelta

def afficher_stats_vehicules(flotte, df_dist):
    st.subheader("🚐 Données sur les véhicules")
    
    # --- NETTOYAGE DE LA MATRICE POUR ÉVITER LE KEYERROR ---
    df_dist_clean = df_dist.copy()
    
    # On définit la première colonne (les noms des sites) comme Index
    nom_col_sites = df_dist_clean.columns[0]
    df_dist_clean = df_dist_clean.set_index(nom_col_sites)
    
    # Nettoyage : Tout en MAJUSCULES et suppression des espaces invisibles
    df_dist_clean.index = df_dist_clean.index.astype(str).str.strip().str.upper()
    df_dist_clean.columns = df_dist_clean.columns.astype(str).str.strip().str.upper()
    # -------------------------------------------------------

    nb_vehicules = len(flotte)
    km_totaux = 0
    nb_chauffeurs = 0
    gantt_data = []

    for v_id, vacations in flotte.items():
        nb_chauffeurs += len(vacations)
        for vacation in vacations:
            for tournee in vacation:
                # Calcul des kilomètres
                for i in range(len(tournee) - 1):
                    # On nettoie aussi les noms venant de la tournée pour la comparaison
                    site_dep = str(tournee[i]['site']).strip().upper()
                    site_arr = str(tournee[i+1]['site']).strip().upper()
                    
                    try:
                        km_totaux += df_dist_clean.loc[site_dep, site_arr]
                    except KeyError:
                        # Si un site n'est toujours pas trouvé, on évite le crash
                        st.error(f"⚠️ Le site '{site_dep}' ou '{site_arr}' est introuvable dans la matrice de distance.")
                
                # Données pour le graphique
                gantt_data.append({
                    "Véhicule": v_id,
                    "Début": tournee[0]['heure'],
                    "Fin": tournee[-1]['heure'],
                    "Type": "Tournée"
                })

    # Affichage des indicateurs (KPIs)
    km_moyen = km_totaux / nb_chauffeurs if nb_chauffeurs > 0 else 0
    c1, c2, c3 = st.columns(3)
    c1.metric("Véhicules nécessaires", f"{nb_vehicules}")
    c2.metric("Distance totale", f"{int(km_totaux)} km")
    c3.metric("Km moyen / chauffeur", f"{int(km_moyen)} km")

    # --- GRAPHIQUE GANTT ---
    fig = go.Figure()
    for entry in gantt_data:
        start_time = str(timedelta(minutes=entry["Début"]))[:-3]
        end_time = str(timedelta(minutes=entry["Fin"]))[:-3]
        
        fig.add_trace(go.Bar(
            base=[entry["Début"]],
            x=[entry["Fin"] - entry["Début"]],
            y=[entry["Véhicule"]],
            orientation='h',
            marker_color='#2E86C1',
            hovertemplate=f"{entry['Type']}: {start_time} - {end_time}<extra></extra>"
        ))

    fig.update_layout(
        title="Occupation des véhicules",
        xaxis=dict(
            title="Heure",
            tickvals=list(range(300, 1321, 60)), # de 5h à 22h
            ticktext=[f"{h//60}h" for h in range(300, 1321, 60)]
        ),
        yaxis_title="Véhicules",
        showlegend=False,
        height=400
    )
    st.plotly_chart(fig, use_container_width=True)

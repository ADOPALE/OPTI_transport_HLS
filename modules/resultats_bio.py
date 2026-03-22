import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta

def afficher_stats_vehicules(flotte, df_dist):
    """
    Calcule et affiche les KPIs et le graphique d'occupation pour les véhicules.
    """
    st.subheader("🚐 Données sur les véhicules")
    
    # 1. Calcul des indicateurs
    nb_vehicules = len(flotte) # Nombre de clés dans le dictionnaire
    
    km_totaux = 0
    nb_chauffeurs = 0
    gantt_data = []

    for v_id, vacations in flotte.items():
        nb_chauffeurs += len(vacations) # Une vacation = un chauffeur
        
        for v_idx, vacation in enumerate(vacations):
            for tournee in vacation:
                # Calcul des km (cumul entre chaque étape de la tournée)
                for i in range(len(tournee) - 1):
                    site_dep = tournee[i]['site']
                    site_arr = tournee[i+1]['site']
                    # On va chercher la distance dans la matrice df_dist
                    km_totaux += df_dist.loc[site_dep, site_arr]
                
                # Préparation données GANTT : Temps occupé (Tournée)
                gantt_data.append({
                    "Véhicule": v_id,
                    "Début": tournee[0]['heure'],
                    "Fin": tournee[-1]['heure'],
                    "Type": "Tournée effectif",
                    "Couleur": "#2E86C1" # Bleu
                })

    km_moyen_chauffeur = km_totaux / nb_chauffeurs if nb_chauffeurs > 0 else 0

    # 2. Affichage des Metrics
    c1, c2, c3 = st.columns(3)
    c1.metric("Véhicules nécessaires", f"{nb_vehicules}")
    c2.metric("Distance totale", f"{int(km_totaux)} km")
    c3.metric("Km moyen / chauffeur", f"{int(km_moyen_chauffeur)} km")

    # 3. Graphique GANTT d'occupation des véhicules
    st.write("**Occupation temporelle des véhicules**")
    
    fig = go.Figure()

    for entry in gantt_data:
        # Transformation des minutes en format heure pour l'axe X
        start = str(timedelta(minutes=entry["Début"]))[:-3]
        end = str(timedelta(minutes=entry["Fin"]))[:-3]
        
        fig.add_trace(go.Bar(
            base=[entry["Début"]],
            x=[entry["Fin"] - entry["Début"]],
            y=[entry["Véhicule"]],
            orientation='h',
            name=entry["Type"],
            marker_color=entry["Couleur"],
            hovertemplate=f"Tournée: {start} - {end}<extra></extra>"
        ))

    fig.update_layout(
        barmode='stack',
        xaxis=dict(
            title="Heure de la journée",
            tickvals=list(range(300, 1261, 60)),
            ticktext=[f"{h//60}h" for h in range(300, 1261, 60)]
        ),
        showlegend=False,
        height=300 + (nb_vehicules * 30)
    )

    st.plotly_chart(fig, use_container_width=True)

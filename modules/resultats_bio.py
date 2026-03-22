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


def afficher_stats_chauffeurs(flotte, config_rh):
    """
    Calcule les indicateurs de performance liés aux chauffeurs (vacations).
    """
    st.subheader("👥 Données sur les chauffeurs")

    nb_postes = 0
    duree_totale_tournees = 0
    total_tournees = 0
    
    # Récupération des contraintes RH
    # Amplitude (ex: 450 min), Pause (ex: 30 min)
    amplitude_max = config_rh.get('amplitude', 450)
    pause_reglementaire = config_rh.get('pause', 30)

    for v_id, vacations in flotte.items():
        nb_postes += len(vacations) # Chaque vacation est un poste chauffeur
        
        for vacation in vacations:
            total_tournees += len(vacation)
            for tournee in vacation:
                # Durée de la tournée = Heure de fin - Heure de début
                duree_trne = tournee[-1]['heure'] - tournee[0]['heure']
                duree_totale_tournees += duree_trne

    # --- CALCUL DES INDICATEURS ---
    
    # 1. Taux d'occupation moyen
    # Formule : Temps de roulage / (Amplitude totale - Temps de pause)
    temps_travail_dispo_par_poste = amplitude_max - pause_reglementaire
    if nb_postes > 0 and temps_travail_dispo_par_poste > 0:
        occupation_moyenne = (duree_totale_tournees / (nb_postes * temps_travail_dispo_par_poste)) * 100
    else:
        occupation_moyenne = 0

    # 2. Moyenne de tournées par poste
    tournees_par_poste = total_tournees / nb_postes if nb_postes > 0 else 0

    # --- AFFICHAGE ---
    c1, c2, c3 = st.columns(3)
    c1.metric("Nombre de postes (7h30)", f"{nb_postes}")
    c2.metric("Taux d'occupation moyen", f"{occupation_moyenne:.1f} %")
    c3.metric("Tournées moyennes / poste", f"{tournees_par_poste:.1f}")

    # Petit graphique de répartition du temps pour un chauffeur type
    if nb_postes > 0:
        temps_moyen_roulage = duree_totale_tournees / nb_postes
        temps_inoccupé = temps_travail_dispo_par_poste - temps_moyen_roulage
        
        fig_pie = px.pie(
            names=["Temps en tournée", "Temps inoccupé / Attente", "Pause réglementaire"],
            values=[temps_moyen_roulage, max(0, temps_inoccupé), pause_reglementaire],
            color_discrete_sequence=["#2E86C1", "#D5D8DC", "#EB984E"],
            title="Répartition moyenne d'une vacation"
        )
        st.plotly_chart(fig_pie, use_container_width=True)

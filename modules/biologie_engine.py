import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timedelta
import sys

# ==========================================
# PARTIE 1 : LOGIQUE DE CALCUL (ALGORITHME)
# ==========================================

def minutes_to_hhmm(minutes):
    """Convertit des minutes depuis minuit en format HH:MM."""
    h = int(minutes // 60)
    m = int(minutes % 60)
    return f"{h:02d}:{m:02d}"

def generate_target_windows(sites_config):
    """
    Génère les rendez-vous théoriques selon la config utilisateur.
    sites_config: dict {nom_site: {'open': int, 'close': int, 'freq': int}}
    """
    tasks = []
    for site_name, config in sites_config.items():
        ouverture, fermeture, freq = config['open'], config['close'], config['freq']
        
        # Calcul de l'intervalle cible
        intervalle = (fermeture - ouverture) / freq
        marge = intervalle * 0.15 # Marge de tolérance de 15%
        
        for i in range(freq):
            cible = ouverture + (i + 0.5) * intervalle
            tasks.append({
                'site_name': site_name,
                'window': (max(ouverture, cible - marge), min(fermeture, cible + marge)),
                'done': False
            })
    # Tri par heure d'ouverture de fenêtre
    return sorted(tasks, key=lambda x: x['window'][0])

def run_optimization(m_duree_df, sites_config, temps_collecte, max_tournee):
    """
    Moteur principal d'optimisation.
    m_duree_df : DataFrame (index/cols = noms sites)
    sites_config : Configuration issue de st.session_state
    """
    tasks = generate_target_windows(sites_config)
    tournees = []
    tasks_copy = [t.copy() for t in tasks]
    
    # On travaille avec les noms de sites pour mapper la matrice
    while any(not t['done'] for t in tasks_copy):
        remaining = [t for t in tasks_copy if not t['done']]
        if not remaining: break
        
        first_task = remaining[0]
        # Départ du HLS calé sur la première tâche
        duree_hls_vers_site = m_duree_df.loc["HLS", first_task['site_name']]
        heure_depart_hls = max(480, first_task['window'][0] - duree_hls_vers_site)
        
        current_time = heure_depart_hls
        tournee = [{'site': "HLS", 'heure': current_time}]
        current_site = "HLS"
        
        while True:
            best_task_idx = None
            score_min = float('inf')
            
            for idx, task in enumerate(tasks_copy):
                if task['done']: continue
                
                trajet = m_duree_df.loc[current_site, task['site_name']]
                retour_hls = m_duree_df.loc[task['site_name'], "HLS"]
                
                # Simulation du passage
                arrivee_site = current_time + trajet
                # On commence la collecte soit à l'arrivée, soit à l'ouverture de la fenêtre
                debut_collecte = max(arrivee_site, task['window'][0])
                fin_collecte = debut_collecte + temps_collecte
                
                # Vérification contrainte de durée (Retour au HLS inclus)
                if (fin_collecte + retour_hls - tournee[0]['heure']) <= max_tournee:
                    # Score : priorité au respect de la fenêtre, puis au trajet court
                    retard = max(0, arrivee_site - task['window'][1])
                    attente = max(0, task['window'][0] - arrivee_site)
                    score = retard * 20 + attente + trajet
                    
                    if score < score_min:
                        score_min, best_task_idx = score, idx
            
            if best_task_idx is not None:
                task = tasks_copy[best_task_idx]
                heure_arrivee = max(current_time + m_duree_df.loc[current_site, task['site_name']], task['window'][0])
                tournee.append({'site': task['site_name'], 'heure': heure_arrivee})
                
                # Le temps avance : Arrivée + Collecte
                current_time = heure_arrivee + temps_collecte
                task['done'] = True
                current_site = task['site_name']
            else:
                # Retour HLS
                tournee.append({'site': "HLS", 'heure': current_time + m_duree_df.loc[current_site, "HLS"]})
                break
        
        tournees.append(tournee)
    
    # Assignation des tournées aux véhicules physiques (Chainage)
    flotte = assign_to_vehicles(tournees)
    return flotte

def assign_to_vehicles(tournees):
    """Regroupe les tournées unitaires pour minimiser le nombre de véhicules."""
    tournees_triees = sorted(tournees, key=lambda x: x[0]['heure'])
    vehicules = []
    
    for trne in tournees_triees:
        assigned = False
        for v in vehicules:
            # Un véhicule peut reprendre si sa fin est <= début nouvelle tournée
            if v[-1][-1]['heure'] <= trne[0]['heure']:
                v.append(trne)
                assigned = True
                break
        if not assigned:
            vehicules.append([trne])
    return vehicules


import pandas as pd
import numpy as np
from datetime import datetime, timedelta

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
    # --- PRÉPARATION ROBUSTE DE LA MATRICE ---
    # Copie pour éviter de modifier l'original dans le session_state
    df = m_duree_df.copy()

    # 1. On s'assure que la matrice utilise les NOMS de sites comme Index
    if 'site' in df.columns:
        df = df.set_index('site')
    elif df.shape[1] > 1:
        # On suppose que la colonne 0 est l'ID et la colonne 1 est le Nom
        nom_colonne_sites = df.columns[1]
        df = df.set_index(nom_colonne_sites)
    
    # 2. Nettoyage des index et colonnes (espaces invisibles)
    df.index = df.index.astype(str).str.strip()
    df.columns = df.columns.astype(str).str.strip()

    # 3. Identification du dépôt (HLS)
    if "HLS" in df.index:
        depot = "HLS"
    else:
        depot = df.index[0]
        print(f"DEBUG: 'HLS' non trouvé. Utilisation de '{depot}' comme dépôt.")

    # --- RESTE DE L'ALGORITHME ---
    tasks = generate_target_windows(sites_config)
    tournees = []
    tasks_copy = [t.copy() for t in tasks]
    
    while any(not t['done'] for t in tasks_copy):
        remaining = [t for t in tasks_copy if not t['done']]
        if not remaining: break
        
        first_task = remaining[0]
        
        # On utilise 'depot' au lieu de "HLS"
        duree_depot_vers_site = df.loc[depot, first_task['site_name']]
        heure_depart_depot = max(480, first_task['window'][0] - duree_depot_vers_site)
        
        current_time = heure_depart_depot
        tournee = [{'site': depot, 'heure': current_time}]
        current_site = depot
        
        while True:
            best_task_idx = None
            score_min = float('inf')
            
            for idx, task in enumerate(tasks_copy):
                if task['done']: continue
                
                trajet = df.loc[current_site, task['site_name']]
                retour_depot = df.loc[task['site_name'], depot]
                
                # Simulation du passage
                arrivee_site = current_time + trajet
                debut_collecte = max(arrivee_site, task['window'][0])
                fin_collecte = debut_collecte + temps_collecte
                
                # Vérification contrainte de durée (Retour au dépôt inclus)
                if (fin_collecte + retour_depot - tournee[0]['heure']) <= max_tournee:
                    # Score : priorité au respect de la fenêtre
                    retard = max(0, arrivee_site - task['window'][1])
                    attente = max(0, task['window'][0] - arrivee_site)
                    score = retard * 20 + attente + trajet
                    
                    if score < score_min:
                        score_min, best_task_idx = score, idx
            
            if best_task_idx is not None:
                task = tasks_copy[best_task_idx]
                # Arrivée réelle (soit trajet, soit attente ouverture fenêtre)
                heure_arrivee = max(current_time + df.loc[current_site, task['site_name']], task['window'][0])
                tournee.append({'site': task['site_name'], 'heure': heure_arrivee})
                
                current_time = heure_arrivee + temps_collecte
                task['done'] = True
                current_site = task['site_name']
            else:
                # Retour final au dépôt
                tournee.append({'site': depot, 'heure': current_time + df.loc[current_site, depot]})
                break
        
        tournees.append(tournee)
    
    # Assignation aux véhicules
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

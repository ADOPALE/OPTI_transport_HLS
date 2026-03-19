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
    # --- PRÉPARATION ULTRA-ROBUSTE DE LA MATRICE ---
    df = m_duree_df.copy()

    # 1. Forcer le passage des noms en Index si ce n'est pas fait
    # On regarde si une colonne contient 'site' ou si c'est la 2ème colonne (index 1)
    if 'site' in df.columns:
        df = df.set_index('site')
    elif df.shape[1] > 1:
        # On prend la deuxième colonne comme index des noms
        df = df.set_index(df.columns[1])
    
    # 2. Nettoyage radical : Tout en string, sans espaces, en MAJUSCULES pour comparer
    df.index = df.index.astype(str).str.strip().str.upper()
    df.columns = df.columns.astype(str).str.strip().str.upper()

    # 3. Identification du dépôt (on cherche HLS ou le premier site)
    if "HLS" in df.index:
        depot = "HLS"
    else:
        depot = df.index[0]
        print(f"DEBUG ENGINE: Dépôt utilisé -> {depot}")

    # 4. Nettoyage de la configuration des tâches
    # On transforme les noms de sites_config pour qu'ils matchent la matrice nettoyée
    clean_sites_config = {str(k).strip().upper(): v for k, v in sites_config.items()}

    # --- RESTE DE L'ALGORITHME ---
    # On passe la config nettoyée à generate_target_windows
    tasks = generate_target_windows(clean_sites_config)
    tournees = []
    tasks_copy = [t.copy() for t in tasks]
    
    while any(not t['done'] for t in tasks_copy):
        remaining = [t for t in tasks_copy if not t['done']]
        if not remaining: break
        
        first_task = remaining[0]
        site_cible = first_task['site_name'] # Déjà en majuscule grâce au nettoyage

        # Vérification si le site existe vraiment dans la matrice pour éviter le crash
        if site_cible not in df.index:
            print(f"ERREUR : Le site {site_cible} est absent de la matrice.")
            first_task['done'] = True # On l'ignore pour ne pas bloquer la boucle
            continue

        duree_depot_vers_site = df.loc[depot, site_cible]
        heure_depart_depot = max(480, first_task['window'][0] - duree_depot_vers_site)
        
        current_time = heure_depart_depot
        tournee = [{'site': depot, 'heure': current_time}]
        current_site = depot
        
        while True:
            best_task_idx = None
            score_min = float('inf')
            
            for idx, task in enumerate(tasks_copy):
                if task['done']: continue
                t_site = task['site_name']
                
                if t_site not in df.index: continue

                trajet = df.loc[current_site, t_site]
                retour_depot = df.loc[t_site, depot]
                
                arrivee_site = current_time + trajet
                debut_collecte = max(arrivee_site, task['window'][0])
                fin_collecte = debut_collecte + temps_collecte
                
                if (fin_collecte + retour_depot - tournee[0]['heure']) <= max_tournee:
                    retard = max(0, arrivee_site - task['window'][1])
                    attente = max(0, task['window'][0] - arrivee_site)
                    score = retard * 20 + attente + trajet
                    if score < score_min:
                        score_min, best_task_idx = score, idx
            
            if best_task_idx is not None:
                task = tasks_copy[best_task_idx]
                t_site = task['site_name']
                heure_arrivee = max(current_time + df.loc[current_site, t_site], task['window'][0])
                tournee.append({'site': t_site, 'heure': heure_arrivee})
                current_time = heure_arrivee + temps_collecte
                task['done'] = True
                current_site = t_site
            else:
                tournee.append({'site': depot, 'heure': current_time + df.loc[current_site, depot]})
                break
        
        tournees.append(tournee)
    
    return assign_to_vehicles(tournees)

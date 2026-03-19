import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# ==========================================
# PARTIE 1 : FONCTIONS UTILITAIRES
# ==========================================

def minutes_to_hhmm(minutes):
    """Convertit des minutes depuis minuit en format HH:MM."""
    h = int(minutes // 60)
    m = int(minutes % 60)
    return f"{h:02d}:{m:02d}"

def assign_to_vehicles(tournees):
    """Regroupe les tournées unitaires pour minimiser le nombre de véhicules."""
    if not tournees:
        return []
    # Tri des tournées par heure de départ
    tournees_triees = sorted(tournees, key=lambda x: x[0]['heure'])
    vehicules = []
    
    for trne in tournees_triees:
        assigned = False
        for v in vehicules:
            # Un véhicule peut reprendre si sa fin est <= début de la nouvelle tournée
            # On ajoute une petite marge de sécurité de 5min si besoin ici
            if v[-1][-1]['heure'] <= trne[0]['heure']:
                v.append(trne)
                assigned = True
                break
        if not assigned:
            vehicules.append([trne])
    return vehicules

def generate_target_windows(sites_config):
    """Génère les rendez-vous théoriques selon la config utilisateur."""
    tasks = []
    for site_name, config in sites_config.items():
        ouverture, fermeture, freq = config['open'], config['close'], config['freq']
        intervalle = (fermeture - ouverture) / freq
        marge = intervalle * 0.15 
        
        for i in range(freq):
            cible = ouverture + (i + 0.5) * intervalle
            tasks.append({
                'site_name': site_name,
                'window': (max(ouverture, cible - marge), min(fermeture, cible + marge)),
                'done': False
            })
    return sorted(tasks, key=lambda x: x['window'][0])

# ==========================================
# PARTIE 2 : MOTEUR PRINCIPAL
# ==========================================

def run_optimization(m_duree_df, sites_config, temps_collecte, max_tournee):
    # --- PRÉPARATION DE LA MATRICE ---
    df = m_duree_df.copy()

    # Nettoyage des colonnes/index pour matcher avec les noms de sites
    if 'site' in df.columns:
        df = df.set_index('site')
    elif df.shape[1] > 1:
        df = df.set_index(df.columns[1])
    
    df.index = df.index.astype(str).str.strip().str.upper()
    df.columns = df.columns.astype(str).str.strip().str.upper()

    # Identification du dépôt
    depot = "HLS" if "HLS" in df.index else df.index[0]
    
    # Nettoyage de la config pour correspondre à la matrice (MAJUSCULES)
    clean_sites_config = {str(k).strip().upper(): v for k, v in sites_config.items()}

    # --- CALCUL ---
    tasks = generate_target_windows(clean_sites_config)
    tournees = []
    tasks_copy = [t.copy() for t in tasks]
    
    while any(not t['done'] for t in tasks_copy):
        remaining = [t for t in tasks_copy if not t['done']]
        if not remaining: break
        
        first_task = remaining[0]
        site_cible = first_task['site_name']

        if site_cible not in df.index:
            first_task['done'] = True
            continue

        duree_hls_vers_site = df.loc[depot, site_cible]
        heure_depart_hls = max(480, first_task['window'][0] - duree_hls_vers_site)
        
        current_time = heure_depart_hls
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

    print(f"Nombre de tournées générées : {len(tournees)}")
    if len(tournees) > 0:
    print(f"Exemple de trajet : {tournees[0]}")
    
    # On appelle enfin la fonction définie plus haut
    return assign_to_vehicles(tournees)

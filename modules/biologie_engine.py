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

def assign_to_vehicles(tournees, config_rh):
    """
    Assigne les tournées à des chauffeurs en respectant les 7h30, 
    la pause et la relève.
    """
    if not tournees:
        return []

    # Paramètres RH
    MAX_POSTE = config_rh.get('amplitude', 450)
    PAUSE = config_rh.get('pause', 30)
    RELEVE = config_rh.get('releve', 15)
    
    tournees_triees = sorted(tournees, key=lambda x: x[0]['heure'])
    
    # Liste des chauffeurs (chaque chauffeur a sa propre liste de tournées)
    chauffeurs = [] 

    for trne in tournees_triees:
        debut_trne = trne[0]['heure']
        fin_trne = trne[-1]['heure']
        assigned = False
        
        for chauffeur in chauffeurs:
            h_debut_premier = chauffeur[0][0]['heure']
            h_fin_dernier = chauffeur[-1][-1]['heure']
            
            # 1. Vérifier si la tournée rentre dans l'amplitude de 7h30 du chauffeur
            if (fin_trne - h_debut_premier) <= MAX_POSTE:
                # 2. Vérifier s'il y a assez de temps pour une pause si on est au milieu du poste
                # On considère que si le chauffeur a déjà travaillé > 3h, il lui faut sa pause
                temps_travail_cumule = h_fin_dernier - h_debut_premier
                marge_necessaire = 0
                if temps_travail_cumule > 180: # > 3h
                    marge_necessaire = PAUSE
                
                if h_fin_dernier + marge_necessaire <= debut_trne:
                    chauffeur.append(trne)
                    assigned = True
                    break
        
        if not assigned:
            # Nouveau chauffeur
            chauffeurs.append([trne])

    # Logique de relève (Optionnel : Regrouper les chauffeurs par "véhicule physique")
    # Pour l'instant, 'chauffeurs' représente le nombre de postes de travail nécessaires.
    return chauffeurs

def generate_target_windows(sites_config):
    """Génère les rendez-vous théoriques (fenêtres) selon la config utilisateur."""
    tasks = []
    for site_name, config in sites_config.items():
        ouv, fer, freq = config['open'], config['close'], config['freq']
        
        # Calcul de l'espacement idéal entre deux passages
        intervalle = (fer - ouv) / freq
        # Marge de souplesse (20% de l'intervalle) pour aider l'algorithme à grouper
        marge = intervalle * 0.20 
        
        for i in range(freq):
            cible = ouv + (i + 0.5) * intervalle
            tasks.append({
                'site_name': str(site_name).strip().upper(),
                'window': (max(ouv, cible - marge), min(fer, cible + marge)),
                'done': False
            })
    # Tri chronologique des besoins
    return sorted(tasks, key=lambda x: x['window'][0])

# ==========================================
# PARTIE 2 : MOTEUR DE CALCUL PRINCIPAL
# ==========================================
def run_optimization(m_duree_df, sites_config, temps_collecte, max_tournee):
    # --- PRÉPARATION DE LA MATRICE ---
    df = m_duree_df.copy()
    nom_colonne_noms = df.columns[0]
    df = df.set_index(nom_colonne_noms)
    df.index = df.index.astype(str).str.strip().str.upper()
    df.columns = df.columns.astype(str).str.strip().str.upper()

    depot = "HLS"
    clean_sites_config = {str(k).strip().upper(): v for k, v in sites_config.items()}

    # --- INITIALISATION ---
    tasks = generate_target_windows(clean_sites_config)
    tournees = []
    tasks_copy = [t.copy() for t in tasks]
    
    while any(not t['done'] for t in tasks_copy):
        remaining = [t for t in tasks_copy if not t['done']]
        if not remaining: break
        
        # On démarre une nouvelle tournée
        first_task = remaining[0]
        site_cible = first_task['site_name']

        if site_cible not in df.index:
            first_task['done'] = True
            continue

        heure_depart = max(300, first_task['window'][0] - df.loc[depot, site_cible])
        current_time = heure_depart
        tournee = [{'site': depot, 'heure': current_time}]
        current_site = depot
        
        # --- SOLUTION : Liste des sites déjà faits DANS CETTE TOURNEE ---
        # On utilise un set pour bloquer le site dès qu'il est visité une fois
        sites_visites_cette_tournee = set()

        while True:
            best_task_idx = None
            score_min = float('inf')
            
            for idx, task in enumerate(tasks_copy):
                t_site = task['site_name']
                
                # CONDITION CRUCIALE : 
                # 1. La tâche n'est pas faite
                # 2. Le site est dans la matrice
                # 3. LE SITE N'A PAS ENCORE ÉTÉ VISITÉ DURANT CETTE TOURNÉE
                if task['done'] or t_site not in df.index or t_site in sites_visites_cette_tournee:
                    continue
                
                trajet = df.loc[current_site, t_site]
                retour = df.loc[t_site, depot]
                arrivee = current_time + trajet
                debut_coll = max(arrivee, task['window'][0])
                fin_coll = debut_coll + temps_collecte
                
                # Vérification durée max
                if (fin_coll + retour - tournee[0]['heure']) <= max_tournee:
                    attente = max(0, task['window'][0] - arrivee)
                    score = attente + (trajet * 2) 
                    
                    if score < score_min:
                        score_min, best_task_idx = score, idx
            
            if best_task_idx is not None:
                task = tasks_copy[best_task_idx]
                t_site = task['site_name']
                
                heure_reelle = max(current_time + df.loc[current_site, t_site], task['window'][0])
                tournee.append({'site': t_site, 'heure': heure_reelle})
                
                current_time = heure_reelle + temps_collecte
                task['done'] = True
                current_site = t_site
                
                # ON VERROUILLE LE SITE POUR CETTE TOURNÉE
                sites_visites_cette_tournee.add(t_site)
                
                # Cas particulier : si plusieurs noms de sites sont au même endroit (durée 0)
                # on verrouille aussi tous les sites qui sont à 0 minute de celui-ci
                for autre_site in df.columns:
                    if df.loc[t_site, autre_site] == 0:
                        sites_visites_cette_tournee.add(autre_site)
            else:
                # Retour au dépôt obligatoire
                tournee.append({'site': depot, 'heure': current_time + df.loc[current_site, depot]})
                break
        
        tournees.append(tournee)
    
    return assign_to_vehicles(tournees)

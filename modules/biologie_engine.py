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
    
    # Tri des tournées par heure de départ au dépôt
    tournees_triees = sorted(tournees, key=lambda x: x[0]['heure'])
    vehicules = []
    
    for trne in tournees_triees:
        assigned = False
        for v in vehicules:
            # Un véhicule peut reprendre une tournée si sa fin est <= début de la suivante
            # On laisse une marge de sécurité de 0 minute ici
            if v[-1][-1]['heure'] <= trne[0]['heure']:
                v.append(trne)
                assigned = True
                break
        if not assigned:
            vehicules.append([trne])
    return vehicules

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
    """
    Calcule les tournées optimales.
    m_duree_df : DataFrame brute (avec noms en colonne 0 ou 1)
    """
    # --- PRÉPARATION DE LA MATRICE (Adapté à votre image) ---
    df = m_duree_df.copy()

    # Si la première colonne contient les noms (HLS, HGRL...), on la met en Index
    nom_colonne_noms = df.columns[0]
    df = df.set_index(nom_colonne_noms)
    
    # Nettoyage radical pour la correspondance (Majuscules + pas d'espaces)
    df.index = df.index.astype(str).str.strip().str.upper()
    df.columns = df.columns.astype(str).str.strip().str.upper()

    # Définition du point central
    depot = "HLS" if "HLS" in df.index else df.index[0]
    
    # Nettoyage de la config utilisateur pour correspondre à la matrice
    clean_sites_config = {str(k).strip().upper(): v for k, v in sites_config.items()}

    # --- INITIALISATION ---
    tasks = generate_target_windows(clean_sites_config)
    tournees = []
    tasks_copy = [t.copy() for t in tasks]
    
    # Boucle principale : tant qu'il reste des passages à faire
    while any(not t['done'] for t in tasks_copy):
        remaining = [t for t in tasks_copy if not t['done']]
        if not remaining: break
        
        # On prend la tâche la plus urgente
        first_task = remaining[0]
        site_cible = first_task['site_name']

        # Si le site est inconnu dans la matrice de durée, on l'annule
        if site_cible not in df.index:
            first_task['done'] = True
            continue

        # Calcul de faisabilité de base
        t_aller = df.loc[depot, site_cible]
        t_retour = df.loc[site_cible, depot]
        if (t_aller + temps_collecte + t_retour) > max_tournee:
            # Ce site est trop loin pour la durée max demandée
            first_task['done'] = True
            continue

        # Création d'une nouvelle tournée
        heure_depart = max(480, first_task['window'][0] - t_aller) # Départ à 8h ou selon fenêtre
        current_time = heure_depart
        tournee = [{'site': depot, 'heure': current_time}]
        current_site = depot
        
        # On essaie de remplir cette tournée au maximum
        while True:
            best_task_idx = None
            score_min = float('inf')
            
            for idx, task in enumerate(tasks_copy):
                if task['done'] or task['site_name'] not in df.index: 
                    continue
                
                t_site = task['site_name']
                trajet = df.loc[current_site, t_site]
                retour = df.loc[t_site, depot]
                
                arrivee = current_time + trajet
                debut_coll = max(arrivee, task['window'][0])
                fin_coll = debut_coll + temps_collecte
                
                # Le véhicule peut-il faire ce site ET rentrer au dépôt à temps ?
                if (fin_coll + retour - tournee[0]['heure']) <= max_tournee:
                    # Calcul d'un score (priorité à l'attente la plus courte)
                    attente = max(0, task['window'][0] - arrivee)
                    score = attente + (trajet * 2) 
                    
                    if score < score_min:
                        score_min, best_task_idx = score, idx
            
            if best_task_idx is not None:
                task = tasks_copy[best_task_idx]
                t_site = task['site_name']
                # Heure d'arrivée (incluant l'attente si arrivée avant ouverture fenêtre)
                heure_reelle = max(current_time + df.loc[current_site, t_site], task['window'][0])
                tournee.append({'site': t_site, 'heure': heure_reelle})
                
                current_time = heure_reelle + temps_collecte
                task['done'] = True
                current_site = t_site
            else:
                # Plus rien à ajouter, on rentre au HLS
                tournee.append({'site': depot, 'heure': current_time + df.loc[current_site, depot]})
                break
        
        tournees.append(tournee)
    
    # Une fois toutes les tournées créées, on les assigne aux véhicules
    return assign_to_vehicles(tournees)

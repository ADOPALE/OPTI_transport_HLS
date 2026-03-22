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
def run_optimization(m_duree_df, sites_config, temps_collecte, max_tournee, config_rh=None):
    """
    Moteur d'optimisation mis à jour pour inclure la gestion des chauffeurs et véhicules.
    """
    # 1. Gestion par défaut de la config RH si non fournie
    if config_rh is None:
        config_rh = {'amplitude': 450, 'pause': 30, 'releve': 15}

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
    tournees_unitaires = []
    tasks_copy = [t.copy() for t in tasks]
    
    # Boucle de création des tournées unitaires (votre logique validée précédemment)
    while any(not t['done'] for t in tasks_copy):
        remaining = [t for t in tasks_copy if not t['done']]
        if not remaining: break
        
        first_task = remaining[0]
        site_cible = first_task['site_name']
        if site_cible not in df.index:
            first_task['done'] = True
            continue

        heure_depart = max(300, first_task['window'][0] - df.loc[depot, site_cible])
        current_time = heure_depart
        tournee = [{'site': depot, 'heure': current_time}]
        current_site = depot
        sites_visites_cette_tournee = set()

        while True:
            best_task_idx = None
            score_min = float('inf')
            
            for idx, task in enumerate(tasks_copy):
                t_site = task['site_name']
                if task['done'] or t_site not in df.index or t_site in sites_visites_cette_tournee:
                    continue
                
                trajet = df.loc[current_site, t_site]
                retour = df.loc[t_site, depot]
                arrivee = current_time + trajet
                debut_coll = max(arrivee, task['window'][0])
                fin_coll = debut_coll + temps_collecte
                
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
                sites_visites_cette_tournee.add(t_site)
                # Bloquer les sites à distance 0
                for autre_site in df.columns:
                    if df.loc[t_site, autre_site] == 0:
                        sites_visites_cette_tournee.add(autre_site)
            else:
                tournee.append({'site': depot, 'heure': current_time + df.loc[current_site, depot]})
                break
        
        tournees_unitaires.append(tournee)

    resultat_initial = assign_to_vehicles(tournees_unitaires, config_rh)
    
    # On tente de compacter les postes
    resultat_optimise = optimiser_postes_chauffeurs(resultat_initial, config_rh)
    
    return resultat_optimise
    

def assign_to_vehicles(tournees, config_rh):
    """
    Répartit les tournées par véhicule et par chauffeur (vacation).
    """
    MAX_POSTE = config_rh.get('amplitude', 450)
    PAUSE = config_rh.get('pause', 30)
    RELEVE = config_rh.get('releve', 15)
    
    tournees_triees = sorted(tournees, key=lambda x: x[0]['heure'])
    flotte_vehicules = {}
    
    for trne in tournees_triees:
        debut_trne = trne[0]['heure']
        fin_trne = trne[-1]['heure']
        assigned = False
        
        for v_id, postes in flotte_vehicules.items():
            dernier_poste = postes[-1]
            h_debut_poste = dernier_poste[0][0]['heure']
            h_fin_poste = dernier_poste[-1][-1]['heure']
            
            # 1. Test ajout au chauffeur actuel
            if (fin_trne - h_debut_poste) <= MAX_POSTE:
                marge = PAUSE if (h_fin_poste - h_debut_poste) > 180 else 0
                if h_fin_poste + marge <= debut_trne:
                    dernier_poste.append(trne)
                    assigned = True
                    break
            
            # 2. Test relève sur le même véhicule
            elif h_fin_poste + RELEVE <= debut_trne:
                postes.append([trne])
                assigned = True
                break
        
        if not assigned:
            v_num = len(flotte_vehicules) + 1
            flotte_vehicules[f"Véhicule {v_num}"] = [[trne]]

    return flotte_vehicules


def optimiser_postes_chauffeurs(flotte, config_rh):
    """
    Tente de fusionner les vacations (postes) sous-utilisées 
    pour réduire le nombre total de chauffeurs.
    """
    MAX_POSTE = config_rh.get('amplitude', 450)
    PAUSE = config_rh.get('pause', 30)
    
    # 1. On extrait toutes les tournées de tous les véhicules
    toutes_les_tournees = []
    for vacations in flotte.values():
        for vacation in vacations:
            toutes_les_tournees.append(vacation)
    
    # 2. On trie les vacations par nombre de tournées (les plus petites d'abord)
    # On essaie de vider les "petits" postes dans les "gros"
    toutes_les_tournees.sort(key=len)
    
    nouvelle_flotte = {}
    
    # On ré-applique une logique de remplissage agressive
    for vacation_a_placer in toutes_les_tournees:
        placed = False
        
        for v_id, postes in nouvelle_flotte.items():
            for poste in postes:
                # Calcul des bornes du poste actuel
                h_debut_poste = poste[0][0]['heure']
                h_fin_poste = poste[-1][-1]['heure']
                
                # Bornes de la vacation qu'on veut fusionner
                debut_v = vacation_a_placer[0][0]['heure']
                fin_v = vacation_a_placer[-1][-1]['heure']
                
                # Test de compatibilité (Amplitude max et respect des pauses)
                nouvelle_amplitude = max(h_fin_poste, fin_v) - min(h_debut_poste, debut_v)
                
                if nouvelle_amplitude <= MAX_POSTE:
                    # Vérification du gap pour la pause ou l'enchaînement
                    if fin_v <= h_debut_poste - 15 or debut_v >= h_fin_poste + 15:
                        poste.extend(vacation_a_placer)
                        poste.sort(key=lambda x: x[0]['heure'])
                        placed = True
                        break
            if placed: break
            
        if not placed:
            # Si on ne peut pas fusionner, on crée un nouveau poste sur un véhicule existant ou nouveau
            v_num = len(nouvelle_flotte) + 1
            nouvelle_flotte[f"Véhicule {v_num}"] = [vacation_a_placer]
            
    return nouvelle_flotte

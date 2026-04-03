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
    """
    Génère les rendez-vous théoriques.
    - 1er passage : au plus tôt à l'heure 'open'.
    - Dernier passage : au plus tôt à l'heure 'close'.
    - Intermédiaires : répartis équitablement.
    """
    tasks = []
    for site_name, config in sites_config.items():
        ouv, fer, freq = config['open'], config['close'], config['freq']
        
        # 1. Calcul des points de passage cibles
        if freq <= 1:
            points_passage = [fer] # Si 1 seul passage, on privilégie la fin de journée
        else:
            # Répartition linéaire : le premier à 'ouv', le dernier à 'fer'
            intervalle = (fer - ouv) / (freq - 1)
            points_passage = [ouv + (i * intervalle) for i in range(freq)]
        
        # Marge de retard autorisée (pour donner de la souplesse à l'algorithme)
        # On autorise par exemple 20 minutes de "mou" après l'heure cible
        marge_retard = 20 

        for i, cible in enumerate(points_passage):
            is_premier = (i == 0)
            is_dernier = (i == len(points_passage) - 1)

            if is_premier or is_dernier:
                # --- CONTRAINTE "AU PLUS TÔT" ---
                # La fenêtre commence exactement à l'heure cible (cible, cible + marge)
                # L'algorithme ne peut pas planifier avant 'cible'
                window = (cible, cible + marge_retard)
            else:
                # Passages intermédiaires : on garde une petite marge avant/après 
                # pour l'optimisation, ou on peut aussi les durcir si besoin.
                window = (cible - 10, cible + 10)

            tasks.append({
                'site_name': str(site_name).strip().upper(),
                'window': window,
                'target_time': cible,
                'is_fixed': is_premier or is_dernier, # Flag utile pour le débug
                'done': False
            })

    # Tri pour le moteur de calcul
    return sorted(tasks, key=lambda x: x['window'][0])




# ==========================================
# PARTIE 2 : MOTEUR DE CALCUL PRINCIPAL
# ==========================================
def run_optimization(m_duree_df, sites_config, temps_collecte, max_tournee, config_rh=None, souplesse=False):
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
    resultat_optimise = optimiser_postes_chauffeurs(resultat_initial, config_rh, souplesse=souplesse)
    
    return resultat_optimise
    

def assign_to_vehicles(tournees, config_rh):
    """Répartit les tournées sur les véhicules sans chevauchement."""
    tournees_triees = sorted(tournees, key=lambda x: x[0]['heure'])
    flotte_vehicule = {} 
    
    for t in tournees_triees:
        placed = False
        h_debut_t = t[0]['heure']
        for v_id in flotte_vehicule:
            # Heure de retour au dépôt de la dernière tournée de ce véhicule
            h_fin_derniere = flotte_vehicule[v_id][-1][-1]['heure']
            # Le véhicule doit être revenu pour être réutilisé
            if h_debut_t >= h_fin_derniere:
                flotte_vehicule[v_id].append(t)
                placed = True
                break
        if not placed:
            v_new = f"V{len(flotte_vehicule)+1}"
            flotte_vehicule[v_new] = [t]
    return flotte_vehicule
    
def optimiser_postes_chauffeurs(flotte_vehicule, config_rh, souplesse=False):
    """
    Remplace intégralement l'ancienne fonction.
    Garantit aucun chevauchement en respectant RELEVE et PAUSE.
    """
    MAX_AMPLITUDE = config_rh.get('amplitude', 450)
    DUREE_PAUSE = config_rh.get('pause', 30)
    DUREE_RELEVE = config_rh.get('releve', 15)
    
    # 1. Extraire toutes les tournées de la flotte véhicule pour les traiter par chauffeur
    all_t = []
    for v_id in flotte_vehicule:
        for t in flotte_vehicule[v_id]:
            all_t.append(t)
    
    # 2. Tri chronologique absolu (INDISPENSABLE pour éviter les chevauchements)
    all_t.sort(key=lambda x: x[0]['heure'])
    
    postes_chauffeurs = {}
    c_count = 1
    
    for t_a_placer in all_t:
        placed = False
        h_debut_t = t_a_placer[0]['heure']
        h_fin_t = t_a_placer[-1]['heure']
        
        for c_id in postes_chauffeurs:
            vacation = postes_chauffeurs[c_id]
            h_debut_vacation = vacation[0][0]['heure']
            h_fin_derniere_t = vacation[-1][-1]['heure']
            
            # 3. Déterminer le repos nécessaire avant cette nouvelle tournée
            # Si le chauffeur a déjà travaillé plus de 3h30 (210 min), on impose la PAUSE
            # Sinon, on applique la RELÈVE minimum
            temps_travail_deja_fait = h_fin_derniere_t - h_debut_vacation
            repos_requis = DUREE_PAUSE if temps_travail_deja_fait > 210 else DUREE_RELEVE
            
            # 4. Vérification de faisabilité
            # - Le début de la nouvelle tournée doit respecter le repos après la fin de la précédente
            # - L'amplitude totale ne doit pas dépasser le max
            if h_debut_t >= (h_fin_derniere_t + repos_requis):
                if (h_fin_t - h_debut_vacation) <= MAX_AMPLITUDE:
                    postes_chauffeurs[c_id].append(t_a_placer)
                    placed = True
                    break
        
        # 5. Si pas de place chez les chauffeurs existants, on crée un nouveau poste
        if not placed:
            postes_chauffeurs[f"CH_{c_count:02d}"] = [t_a_placer]
            c_count += 1
            
    return postes_chauffeurs

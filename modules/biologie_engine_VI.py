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
    

def optimiser_postes_chauffeurs(flotte, config_rh, souplesse=False):
    """
    Tente de fusionner les vacations pour réduire le nombre de chauffeurs
    SANS ajouter de nouveaux véhicules.
    """
    MAX_POSTE = config_rh.get('amplitude', 450)
    PAUSE = config_rh.get('pause', 30)
    RELEVE = config_rh.get('releve', 15)

    # 1. On récupère la liste de TOUTES les vacations (postes chauffeurs)
    toutes_vacations = []
    for v_id, vacations in flotte.items():
        for vac in vacations:
            toutes_vacations.append(vac)

    # 2. On trie par heure de début pour garder une cohérence chronologique
    toutes_vacations.sort(key=lambda x: x[0][0]['heure'])

    # 3. Nouvelle structure de flotte (on réutilise le même nombre de véhicules max)
    nb_vehicules_max = len(flotte)
    nouvelle_flotte = {f"Véhicule {i+1}": [] for i in range(nb_vehicules_max)}

    for vac_a_placer in toutes_vacations:
        debut_v = vac_a_placer[0][0]['heure']
        fin_v = vac_a_placer[-1][-1]['heure']
        placed = False

        # On essaie d'abord de l'ajouter à un chauffeur existant (Fusion de poste)
        for v_id, postes in nouvelle_flotte.items():
            for poste in postes:
                h_debut_poste = poste[0][0]['heure']
                h_fin_poste = poste[-1][-1]['heure']

                # Test d'amplitude si on fusionne
                nouvelle_amp = max(h_fin_poste, fin_v) - min(h_debut_poste, debut_v)

                if nouvelle_amp <= MAX_POSTE:
                    if souplesse:
                        # Tester des décalages progressifs de 20 à 30 minutes
                        for decalage in range(20, 31):  # De 20 à 30 minutes
                            if (fin_v + decalage >= h_debut_poste and debut_v <= h_fin_poste + decalage):
                                poste.extend(vac_a_placer)
                                poste.sort(key=lambda x: x[0]['heure'])
                                placed = True
                                break
                    else:
                        # Logique stricte actuelle
                        if fin_v + 5 <= h_debut_poste or debut_v >= h_fin_poste + 5:
                            poste.extend(vac_a_placer)
                            poste.sort(key=lambda x: x[0]['heure'])
                            placed = True
                            break
                if placed:
                    break
            if placed:
                break

        # Si fusion impossible, on essaie de créer une nouvelle vacation (nouveau chauffeur)
        # sur un véhicule existant (Relève)
        if not placed:
            for v_id in nouvelle_flotte:
                postes = nouvelle_flotte[v_id]
                if not postes:  # Véhicule vide
                    postes.append(vac_a_placer)
                    placed = True
                    break
                else:
                    # Test si on peut ajouter une relève sur ce véhicule
                    conflit = False
                    for poste in postes:
                        h_dep = poste[0][0]['heure']
                        h_fin = poste[-1][-1]['heure']
                        # Si chevauchement avec une marge de relève
                        if not (fin_v + RELEVE <= h_dep or debut_v >= h_fin + RELEVE):
                            conflit = True
                            break

                    if not conflit:
                        postes.append(vac_a_placer)
                        postes.sort(key=lambda x: x[0][0]['heure'])
                        placed = True
                        break

        # Sécurité : Si vraiment on ne peut pas placer (ne devrait pas arriver à iso-véhicules)
        if not placed:
            v_id_secu = list(nouvelle_flotte.keys())[0]
            nouvelle_flotte[v_id_secu].append(vac_a_placer)

    # Nettoyage des véhicules qui seraient devenus vides après compactage
    return {k: v for k, v in nouvelle_flotte.items() if v}

import pandas as pd
import numpy as np
import math
import streamlit as st 
from datetime import datetime, timedelta
import copy 

# Imports de tes modules existants
from modules.Prep_simul_flux import calculer_capacite_max, to_decimal_minutes, identifier_meilleur_vehicule

# =================================================================
# CLASSE DE BASE
# =================================================================
class Job:
    def __init__(self, job_id, flux_id, type_job, origin, destination, 
                 h_dispo, h_deadline, quantite, contenant, 
                 vehicule_type, type_propre_sale, aller_retour):
        
        self.job_id = job_id          
        self.flux_id = flux_id        
        self.type_job = type_job      
        
        # Noms exacts pour la matrice
        self.origin = str(origin).strip().upper()
        self.destination = str(destination).strip().upper()
        
        # Logique de regroupement (Macro-sites)
        self.origin_group = "HUB_HSJ" if "HSJ_" in self.origin else self.origin
        self.dest_group = "HUB_HSJ" if "HSJ_" in self.destination else self.destination
        
        # PROTECTION CRUCIALE : On force vehicule_type en String simple
        if isinstance(vehicule_type, (pd.Series, list)):
            self.vehicule_type = str(vehicule_type[0]).strip().upper() if len(vehicule_type) > 0 else "INCONNU"
        else:
            self.vehicule_type = str(vehicule_type).strip().upper()

        self.h_dispo = h_dispo        
        self.h_deadline = h_deadline  
        self.quantite = quantite
        self.contenant = contenant
        self.type_propre_sale = type_propre_sale
        self.aller_retour = aller_retour 
        self.est_planifie = False
        self.poids_temps = 0     

# =================================================================
# FONCTIONS DE CALCUL SANITAIRE ET OPPORTUNISTE
# =================================================================

def calculer_delta_temps_mission(p, sj, matrice_duree, t_nettoyage):
    """
    Calcule le trajet d'approche en intégrant le détour par HUB_HSJ 
    si un nettoyage (Sale -> Propre) est nécessaire.
    """
    site_dep_sj = sj['jobs'][0].origin.upper()
    
    # Trajet d'approche standard (direct)
    t_approche = matrice_duree.loc[p['pos'], site_dep_sj]
    besoin_nettoyage = False
    
    # Règle Sanitaire : Un camion SALE doit passer par HSJ pour devenir PROPRE
    if p['dernier_type_sanitaire'] == "SALE" and sj['jobs'][0].type_propre_sale == "PROPRE":
        besoin_nettoyage = True
        t_vers_hsj = matrice_duree.loc[p['pos'], "HUB_HSJ"]
        t_hsj_vers_dep = matrice_duree.loc["HUB_HSJ", site_dep_sj]
        # Nouveau trajet : Pos actuelle -> HSJ -> Nettoyage -> Départ Job
        t_approche = t_vers_hsj + t_nettoyage + t_hsj_vers_dep

    return t_approche, besoin_nettoyage

def calculer_score_opportuniste(p, sj, matrice_duree, t_nettoyage, h_limite_avance=30):
    """
    Arbitre entre :
    1. Attendre sur place (Temps mort)
    2. Avancer le job (max 30min avant l'heure lissée)
    3. Trajet à vide vers un autre site
    """
    t_approche, nettoyage = calculer_delta_temps_mission(p, sj, matrice_duree, t_nettoyage)
    
    h_arrivee_site = p['h_dispo'] + t_approche
    h_lissee = sj['h_depart_actuelle']
    h_prete = sj['h_dispo_max']
    
    # Heure de départ autorisée : au plus tôt entre la marchandise prête et (Lissage - 30min)
    h_dep_autorisee = max(h_prete, h_lissee - h_limite_avance)
    
    # Heure réelle de départ pour ce chauffeur
    h_dep_reel = max(h_arrivee_site, h_dep_autorisee)
    
    # Coût de l'attente (temps mort)
    t_attente = max(0, h_dep_reel - h_arrivee_site)
    
    # Pénalité si on dévie du lissage (on préfère rester proche de l'heure cible)
    penalite_avance = max(0, h_lissee - h_dep_reel) * 0.5
    
    # Score final : plus il est bas, plus l'affectation est rentable
    score = t_approche + t_attente + penalite_avance + (2000 if nettoyage else 0)
    
    return score, h_dep_reel, nettoyage

# =================================================================
# MOTEUR DE SÉQUENÇAGE ET ORDONNANCEMENT
# =================================================================

def ordonnancer_flotte_optimale(couloirs, matrice_duree):
    """
    Cherche le nombre minimal de CAMIONS nécessaires en autorisant la relève de chauffeurs.
    Conserve les éléments de débogage et adapte la structure Camion -> Postes.
    """
    if "params_logistique" not in st.session_state:
        st.error("❌ Paramètres logistiques introuvables dans la session.")
        return None

    params = st.session_state["params_logistique"]
    rh = params["rh"]

    # --- ADAPTATION DES PARAMÈTRES ---
    try:
        h_prise_min = to_decimal_minutes(rh["h_prise_min"])
        h_fin_max = to_decimal_minutes(rh["h_fin_max"])
        duree_poste_max = rh["amplitude_totale"]
        
        # Division du temps fixe (prise de service / fin de service)
        t_prepa = rh["temps_fixes"] / 2  
        t_fin_poste = rh["temps_fixes"] / 2
        
        depot = params.get("stationnement_initial", "HLS").upper()
    except KeyError as e:
        st.error(f"❌ Erreur : La clé {e} est absente de params_logistique.")
        return None

    # --- MISE À PLAT DES JOBS ---
    tous_les_jobs = []
    if isinstance(couloirs, dict):
        for sens_dict in couloirs.values():
            if isinstance(sens_dict, dict):
                for liste_sj in sens_dict.values():
                    tous_les_jobs.extend(liste_sj)
            else:
                tous_les_jobs.extend(sens_dict)
    else:
        tous_les_jobs = couloirs

    if not tous_les_jobs:
        st.warning("⚠️ Aucun job à ordonnancer.")
        return None

    # Tri par heure de départ prévue (important pour la logique greedy)
    tous_les_jobs.sort(key=lambda x: x.get('h_depart_actuelle', x.get('h_dispo_max', 0)))

    # --- STATISTIQUES DE DÉBOGGAGE ---
    df_debug = pd.DataFrame([
        {
            "ID": sj['id_super_job'],
            "Type Véhicule": sj['jobs'][0].vehicule_type,
            "Poids (min)": sj['poids_total'],
            "Dispo": sj['h_dispo_max'],
            "Deadline": sj['h_deadline_min']
        } for sj in tous_les_jobs
    ])
    
    st.write("### 📊 Récapitulatif des Super Jobs à ordonnancer")
    st.dataframe(df_debug.groupby("Type Véhicule").size().reset_index(name='Nombre de Jobs'))

    # --- ANALYSE DE FAISABILITÉ AVANT BOUCLE ---
    impossibles = []
    for sj in tous_les_jobs:
        if sj['poids_total'] > duree_poste_max:
            impossibles.append(f"❌ {sj['id_super_job']} : Trop long ({sj['poids_total']} min > {duree_poste_max} min)")
        
        h_fin_theorique = sj['h_depart_actuelle'] + sj['poids_total']
        if h_fin_theorique > sj['h_deadline_min']:
            impossibles.append(f"❌ {sj['id_super_job']} : Deadline impossible (Finit à {h_fin_theorique:.1f} min, Max {sj['h_deadline_min']} min)")
    
    if impossibles:
        st.error("### 🚨 Jobs impossibles détectés (l'ordonnancement va échouer) !")
        for msg in impossibles:
            st.write(msg)
        return {"succes": False, "impossibles": impossibles}

    # --- BOUCLE DE RECHERCHE DU NOMBRE DE CAMIONS ---
    # On commence à 1 et on monte jusqu'à ce que ça passe (plus rapide que de descendre)
    solution_interne = None
    n_max = len(tous_les_jobs)
    st.info(f"🔄 Recherche du nombre minimal de camions (Max théorique : {n_max})...")

    for n_test in range(1, n_max + 1):
        res = tenter_sequencage(
            n_test, tous_les_jobs, depot, matrice_duree, 
            h_prise_min, h_fin_max, duree_poste_max, t_prepa, t_fin_poste
        )
        
        if res["succes"]:
            st.success(f"✅ Solution trouvée avec {n_test} camions !")
            solution_interne = res
            break 

    # --- FORMATAGE DU RÉSULTAT POUR L'INTERFACE ---
    if solution_interne:
        tous_les_postes = []
        # On extrait chaque chauffeur (poste) de chaque camion
        for c in solution_interne['camions']:
            for p in c['postes']:
                # On enrichit le poste avec l'ID du camion pour le Gantt / Tableaux
                p['id_camion'] = c['id_camion']
                tous_les_postes.append(p)
        
        return {
            "succes": True,
            "n_camions": len(solution_interne['camions']),
            "postes": tous_les_postes,
            "details_camions": solution_interne['camions']
        }

    st.error("❌ Aucun ordonnancement n'a pu être trouvé, même avec un camion par job.")
    return {"succes": False}



def tenter_sequencage(n_camions, jobs_a_faire, depot, matrice_duree, h_start, h_limite, max_poste, t_prepa, t_fin, v_type):
    """
    Tente de caser les jobs pour un type de véhicule spécifique (v_type) sans aucune marge de dépassement.
    """
    camions = []
    for i in range(n_camions):
        camions.append({
            'id_camion': f"{v_type}_{i+1:02d}",
            'type': v_type,
            'pos_actuelle': depot,
            'h_dispo_vehicule': h_start,
            'postes': []
        })

    jobs_copy = sorted(copy.deepcopy(jobs_a_faire), key=lambda x: x.get('h_depart_actuelle', 0))

    for sj in jobs_copy:
        attribue = False
        raison_echec = "Inconnu"
        
        for c in camions:
            besoin_nouveau_chauffeur = False
            
            # A. Test sur le chauffeur actuel du camion
            if not c['postes'] or c['postes'][-1]['fini']:
                besoin_nouveau_chauffeur = True
            else:
                p_act = c['postes'][-1]
                score, h_dep, net = calculer_score_opportuniste(p_act, sj, matrice_duree, t_fin)
                h_fin_m = h_dep + sj['poids_total']
                h_ret = h_fin_m + matrice_duree.loc[sj['jobs'][-1].destination.upper(), depot] + t_fin
                debut = p_act['h_debut_service'] if p_act['h_debut_service'] is not None else (h_dep - t_prepa)
                
                # CONDITION STRICTE : Pas une minute de plus que l'amplitude ou la fin d'exploitation
                if (h_ret - debut > max_poste) or (h_ret > h_limite):
                    p_act['fini'] = True
                    besoin_nouveau_chauffeur = True
            
            # B. Tentative avec Relève (Nouveau chauffeur)
            if besoin_nouveau_chauffeur:
                h_dispo_v = max(c['h_dispo_vehicule'], h_start)
                p_neuf = {
                    'id_chauffeur': f"{c['id_camion']}_CH_{len(c['postes'])+1}",
                    'v_type': v_type,
                    'h_debut_service': None,
                    'h_dispo': h_dispo_v + t_prepa,
                    'pos': c['pos_actuelle'],
                    'missions': [],
                    'amplitude': 0,
                    'fini': False,
                    'dernier_type_sanitaire': None,
                    'total_nettoyages': 0
                }
                
                score, h_dep, net = calculer_score_opportuniste(p_neuf, sj, matrice_duree, t_fin)
                h_fin_m = h_dep + sj['poids_total']
                h_ret = h_fin_m + matrice_duree.loc[sj['jobs'][-1].destination.upper(), depot] + t_fin
                
                # TESTS DE FAISABILITÉ STRICTS
                if h_fin_m > sj['h_deadline_min']:
                    continue
                if (h_ret - (h_dep - t_prepa)) > max_poste:
                    continue
                if h_ret > h_limite:
                    continue
                
                p_neuf['h_debut_service'] = h_dep - t_prepa
                c['postes'].append(p_neuf)
                p_cible = c['postes'][-1]
            else:
                p_cible = c['postes'][-1]

            # C. Attribution
            score, h_dep, net = calculer_score_opportuniste(p_cible, sj, matrice_duree, t_fin)
            h_fin_m = h_dep + sj['poids_total']
            
            p_cible['missions'].append({'sj': sj, 'h_dep': h_dep, 'h_fin': h_fin_m, 'nettoyage_effectue': net})
            p_cible['pos'] = sj['jobs'][-1].destination.upper()
            p_cible['h_dispo'] = h_fin_m
            p_cible['dernier_type_sanitaire'] = sj['jobs'][0].type_propre_sale
            if net: p_cible['total_nettoyages'] += 1
            
            c['pos_actuelle'] = p_cible['pos']
            c['h_dispo_vehicule'] = h_fin_m
            
            h_ret_final = h_fin_m + matrice_duree.loc[p_cible['pos'], depot] + t_fin
            p_cible['amplitude'] = h_ret_final - p_cible['h_debut_service']
            
            attribue = True
            break
            
        if not attribue:
            return {"succes": False}

    return {"succes": True, "camions": camions}

def eclater_flux_par_vehicule(df_sequence_type, df_sites, df_vehicules, df_contenants):
    df_travail = df_sequence_type.copy()
    col_site = df_sites.columns[0] 
    
    vehicules_cibles = []
    capacites_max = []

    for _, flux in df_travail.iterrows():
        site_dep = str(flux['Point de départ']).strip()
        site_arr = str(flux['Point de destination']).strip()
        type_cont = str(flux['Nature de contenant']).strip()
        
        v_elu, capa = identifier_meilleur_vehicule(
            site_dep, site_arr, type_cont, 
            df_vehicules, df_contenants, df_sites, col_site
        )
        
        if v_elu is not None:
            # Sécurité : Extraire la valeur texte proprement
            # Si v_elu est une ligne de DataFrame (Series), v_elu['Types'] peut encore être une Series
            valeur_type = v_elu['Types']
            if isinstance(valeur_type, pd.Series):
                valeur_type = valeur_type.iloc[0]
            
            vehicules_cibles.append(str(valeur_type).strip().upper())
            capacites_max.append(capa)
        else:
            vehicules_cibles.append("INCONNU")
            capacites_max.append(0)

    df_travail['vehicule_cible'] = vehicules_cibles
    df_travail['capa_max_vehicule'] = capacites_max
    
    # On éclate en sous-tableaux
    sous_problemes = {
        str(v_type): df_travail[df_travail['vehicule_cible'] == v_type].copy()
        for v_type in df_travail['vehicule_cible'].unique() if v_type != "INCONNU"
    }
    
    return sous_problemes




"""
Transforme chaque ligne de flux en N jobs complets et au max 1 job incomplet.
"""

def fragmenter_flux_en_jobs(df_sous_probleme, h_prise_min, h_fin_max):
    """
    Transforme chaque ligne de flux en N jobs complets et au max 1 job incomplet.
    """
    jobs_complets = []
    jobs_incomplets = []
    
    start_global = to_decimal_minutes(h_prise_min)
    end_global = to_decimal_minutes(h_fin_max)
    
    job_counter = 0

    for index, flux in df_sous_probleme.iterrows():
        # Extraction des données de base
        qte_totale = int(flux['Quantité_Séquence_Type'])
        capa_v = int(flux['capa_max_vehicule'])
        
        # Si capa_max est 0, on ne peut rien faire (sécurité)
        if capa_v <= 0:
            continue
            
        # Calcul des horaires
        h_dispo = to_decimal_minutes(flux.get("Heure de mise à disposition min départ")) if pd.notna(flux.get("Heure de mise à disposition min départ")) else start_global
        h_deadline = to_decimal_minutes(flux.get("Heure max de livraison à la destination")) if pd.notna(flux.get("Heure max de livraison à la destination")) else end_global
        
        # --- A. Création des jobs COMPLETDS ---
        nb_complets = qte_totale // capa_v
        for _ in range(nb_complets):
            job_counter += 1
            new_job = Job(
                job_id=f"JOB_C_{job_counter}",
                flux_id=index,
                type_job='COMPLET',
                origin=str(flux['Point de départ']).strip(),
                destination=str(flux['Point de destination']).strip(),
                h_dispo=h_dispo,
                h_deadline=h_deadline,
                quantite=capa_v,
                contenant=str(flux['Nature de contenant']).strip(),
                vehicule_type=str(flux['vehicule_cible']).strip(),
                type_propre_sale=str(flux.get('Type (propre/sale)', 'Inconnu')).strip(),
                aller_retour=str(flux.get('Aller/Retour', 'Aller')).strip()
            )
            jobs_complets.append(new_job)
            
        # --- B. Création du job INCOMPLET (le reste) ---
        reste = qte_totale % capa_v
        if reste > 0:
            job_counter += 1
            new_job = Job(
                job_id=f"JOB_I_{job_counter}",
                flux_id=index,
                type_job='INCOMPLET',
                origin=str(flux['Point de départ']).strip(),
                destination=str(flux['Point de destination']).strip(),
                h_dispo=h_dispo,
                h_deadline=h_deadline,
                quantite=reste,
                contenant=str(flux['Nature de contenant']).strip(),
                vehicule_type=str(flux['vehicule_cible']).strip(),
                type_propre_sale=str(flux.get('Type (propre/sale)', 'Inconnu')).strip(),
                aller_retour=str(flux.get('Aller/Retour', 'Aller')).strip()
            )
            jobs_incomplets.append(new_job)
            
    return jobs_complets, jobs_incomplets




"""
Vérifie si une liste de jobs (mix de contenants) rentre physiquement dans un véhicule.
Limite le test à la surface au sol et aux dimensions, en mode "First Fit" simple.
"""
def verifier_bin_packing_mixte(vehicule, liste_jobs, df_contenants):
    # 1. Extraction des dimensions du véhicule
    L_v = vehicule['dim longueur interne (m)']
    l_v = vehicule['dim largeur interne (m)']
    poids_max = vehicule['Poids max chargement']
    
    poids_total = 0
    surfaces_objets = []
    
    # 2. Préparation des dimensions de chaque unité de chaque job
    for job in liste_jobs:
        try:
            cont_info = df_contenants[df_contenants['libellé'].str.strip().str.upper() == job.contenant.upper()].iloc[0]
            L_c = cont_info['dim longueur (m)']
            l_c = cont_info['dim largeur (m)']
            poids_c = cont_info['Poids plein (T)']
            
            for _ in range(job.quantite):
                surfaces_objets.append((L_c, l_c))
                poids_total += poids_c
        except:
            return False # Contenant inconnu

    # 3. Vérification immédiate du poids
    if poids_total > poids_max:
        return False

    # 4. Vérification simplifiée par surface au sol (Filtre 1)
    surf_totale_objets = sum(item[0] * item[1] for item in surfaces_objets)
    if surf_totale_objets > (L_v * l_v):
        return False

    # 5. Algorithme de placement "Next Fit Decreasing Height" (NFDH)
    # On trie les objets par hauteur décroissante pour optimiser le rangement en rangées
    surfaces_objets.sort(key=lambda x: max(x), reverse=True)
    
    x_curr, y_curr, h_ligne_max = 0, 0, 0
    for w, h in surfaces_objets:
        # On teste l'objet dans le sens qui prend le moins de largeur
        obj_w, obj_h = (min(w, h), max(w, h))
        
        if x_curr + obj_w > l_v: # On passe à la ligne suivante
            x_curr = 0
            y_curr += h_ligne_max
            h_ligne_max = 0
            
        if y_curr + obj_h > L_v: # Ça ne rentre plus
            return False
            
        x_curr += obj_w
        h_ligne_max = max(h_ligne_max, obj_h)
        
    return True




"""
Cherche les partenaires idéaux pour un job pivot vers une destination commune.
Vérifie la compatibilité sanitaire (Propre/Sale) et le bin-packing.
"""
def trouver_meilleure_comb_dest(job_pivot, pool_candidats, df_vehicules, df_contenants, matrice_duree):
    """Groupe par destination (Collecte multi-points) avec vérification temporelle"""
    v_type = str(job_pivot.vehicule_type).strip().upper()
    col_nom_v = df_vehicules.columns[0]
    
    mask_v = df_vehicules[col_nom_v].str.strip().str.upper() == v_type
    if not mask_v.any():
        return [], 9999
    vehicule = df_vehicules[mask_v].iloc[0]
    
    candidats = [j for j in pool_candidats if 
                 j.dest_group == job_pivot.dest_group and 
                 j.type_propre_sale == job_pivot.type_propre_sale and
                 str(j.vehicule_type).strip().upper() == v_type]
    
    meilleure_comb = []
    try:
        poids_min = matrice_duree.loc[job_pivot.origin, job_pivot.destination] + 10
    except KeyError:
        return [], 9999

    comb_test = [job_pivot]
    for c in candidats:
        if len(comb_test) >= 3: break
        
        # 1. Vérification physique (Bin Packing)
        if verifier_bin_packing_mixte(vehicule, comb_test + [c], df_contenants):
            comb_test_temp = comb_test + [c]
            
            # 2. Calcul du nouveau temps de trajet (poids)
            points_dep = list(set([j.origin for j in comb_test_temp]))
            try:
                poids_test = 0
                curr = points_dep[0]
                for next_pt in points_dep[1:]:
                    poids_test += matrice_duree.loc[curr, next_pt] + 10 
                    curr = next_pt
                poids_test += matrice_duree.loc[curr, job_pivot.destination] + 10
                
                # 3. VERIFICATION TEMPORELLE CRITIQUE
                h_dispo_groupe = max(j.h_dispo for j in comb_test_temp)
                h_deadline_groupe = min(j.h_deadline for j in comb_test_temp)
                
                if h_dispo_groupe + poids_test <= h_deadline_groupe:
                    comb_test = comb_test_temp
                    meilleure_comb = comb_test[1:] 
                    poids_min = poids_test
                else:
                    continue # Trop long pour la deadline
            except KeyError:
                continue
    return meilleure_comb, poids_min





"""
Cherche à grouper des jobs partant du même quai vers des destinations différentes (Tournée).
Ordonne les destinations par proximité pour minimiser les kilomètres et respecte la compatibilité sanitaire.
"""
def trouver_meilleure_comb_dep(job_pivot, pool_candidats, df_vehicules, df_contenants, matrice_duree):
    """Groupe par départ (Tournée de distribution) avec vérification temporelle"""
    v_type = str(job_pivot.vehicule_type).strip().upper()
    col_nom_v = df_vehicules.columns[0]
    
    mask_v = df_vehicules[col_nom_v].str.strip().str.upper() == v_type
    if not mask_v.any():
        return [], 9999
    vehicule = df_vehicules[mask_v].iloc[0]
    
    candidats = [j for j in pool_candidats if 
                 j.origin_group == job_pivot.origin_group and 
                 j.type_propre_sale == job_pivot.type_propre_sale and
                 str(j.vehicule_type).strip().upper() == v_type]
    
    meilleure_comb = []
    poids_min = 9999
    
    # Init poids par défaut
    try:
        poids_min = matrice_duree.loc[job_pivot.origin, job_pivot.destination] + 10
    except:
        pass

    comb_test = [job_pivot]
    for c in candidats:
        if len(comb_test) >= 3: break
        
        # 1. Vérification physique
        if verifier_bin_packing_mixte(vehicule, comb_test + [c], df_contenants):
            comb_test_temp = comb_test + [c]
            dests_uniques = list(set([j.destination for j in comb_test_temp]))
            
            try:
                poids_test = 0
                curr = job_pivot.origin
                temp_dests = dests_uniques.copy()
                while temp_dests:
                    proche = min(temp_dests, key=lambda d: matrice_duree.loc[curr, d])
                    poids_test += matrice_duree.loc[curr, proche] + 10 
                    curr = proche
                    temp_dests.remove(proche)
                
                # 2. VERIFICATION TEMPORELLE CRITIQUE
                h_dispo_groupe = max(j.h_dispo for j in comb_test_temp)
                h_deadline_groupe = min(j.h_deadline for j in comb_test_temp)
                
                if h_dispo_groupe + poids_test <= h_deadline_groupe:
                    comb_test = comb_test_temp
                    meilleure_comb = comb_test[1:]
                    poids_min = poids_test
                else:
                    continue
            except KeyError:
                continue
            
    return meilleure_comb, poids_min




"""
Orchestre l'appairage global de tous les jobs incomplets. 
Pour chaque job, teste les deux logiques de combinaison et retient la plus performante 
en termes de temps de mobilisation (poids).
"""
def appairer_tous_les_jobs_incomplets(liste_incomplets, df_vehicules, df_contenants, matrice_duree):
    pool_restant = liste_incomplets.copy()
    liste_super_jobs_finale = []
    
    # On trie par h_dispo pour traiter les flux chronologiquement (priorité au premier prêt)
    pool_restant.sort(key=lambda x: x.h_dispo)
    
    while len(pool_restant) > 0:
        # On extrait le premier job de la liste (le pivot)
        job_pivot = pool_restant.pop(0)
        
        # 1. On cherche la meilleure combinaison par DESTINATION (même point d'arrivée)
        partenaires_dest, poids_dest = trouver_meilleure_comb_dest(
            job_pivot, pool_restant, df_vehicules, df_contenants, matrice_duree
        )
        
        # 2. On cherche la meilleure combinaison par DEPART (même point d'origine)
        partenaires_dep, poids_dep = trouver_meilleure_comb_dep(
            job_pivot, pool_restant, df_vehicules, df_contenants, matrice_duree
        )
        
        # 3. Arbitrage : Quelle logique est la plus "légère" en temps ?
        # Si poids_dest <= poids_dep, on choisit le groupage destination
        if poids_dest <= poids_dep:
            elus = [job_pivot] + partenaires_dest
            poids_final = poids_dest
            type_comb = "DESTINATION"
        else:
            elus = [job_pivot] + partenaires_dep
            poids_final = poids_dep
            type_comb = "DEPART"
            
        # 4. Création du Super Job (Dictionnaire pour faciliter le lissage ensuite)
        super_job = {
            'id_super_job': f"SJ_{elus[0].job_id}",
            'jobs': elus,                      # Liste des 1, 2 ou 3 objets Job
            'poids_total': poids_final,        # Temps camion mobilisé
            'type_combinaison': type_comb,
            'h_dispo_max': max(j.h_dispo for j in elus), # Le camion ne peut partir que quand TOUT est prêt
            'h_deadline_min': min(j.h_deadline for j in elus) # Le camion doit arriver pour le plus urgent
        }
        
        liste_super_jobs_finale.append(super_job)
        
        # 5. Mise à jour du pool : on retire les jobs qui ont été appairés
        id_elus = [j.job_id for j in elus[1:]] # On ignore le pivot car il est déjà pop()
        pool_restant = [j for j in pool_restant if j.job_id not in id_elus]
        
    return liste_super_jobs_finale



"""
Fusionne les jobs complets et les super-jobs d'incomplets dans une liste unique.
Chaque job complet devient un Super Job à part entière pour uniformiser le traitement.
"""
def preparer_liste_tous_super_jobs(jobs_complets, super_jobs_incomplets, matrice_duree):
    """Fusionne et sécurise les jobs complets et groupés"""
    liste_finale = []
    
    # 1. Traitement des jobs complets
    for j in jobs_complets:
        try:
            poids = matrice_duree.loc[j.origin, j.destination] + 10
        except KeyError:
            st.error(f"❌ Site manquant dans la matrice : {j.origin} ou {j.destination}")
            poids = 30 
        
        # SÉCURITÉ : Vérifier si le job complet est réalisable en direct
        if j.h_dispo + poids > j.h_deadline:
            st.warning(f"⚠️ JOB IMPOSSIBLE (Excel) : {j.job_id} ({j.origin}->{j.destination}). "
                       f"Prêt à {j.h_dispo} min, trajet {poids} min, mais deadline à {j.h_deadline} min. Ignoré.")
            continue
            
        sj = {
            "id_super_job": f"SJ_C_{j.job_id}",
            "jobs": [j],
            "poids_total": poids,
            "type_combinaison": "DIRECT_COMPLET",
            "h_dispo_max": j.h_dispo,
            "h_deadline_min": j.h_deadline
        }
        liste_finale.append(sj)
        
    # 2. Ajout des super jobs issus des incomplets (déjà filtrés par les fonctions précédentes)
    liste_finale.extend(super_jobs_incomplets)
    
    return liste_finale


"""
Regroupe les Super Jobs par couloirs géographiques bidirectionnels.
Normalise tous les sites contenant 'HSJ' en un hub unique pour faciliter l'appairage.
"""
def cartographier_couloirs(liste_tous_super_jobs):
    couloirs = {} # Clé : frozenset({SiteA, SiteB}), Valeur : {'A_vers_B': [], 'B_vers_A': []}

    for sj in liste_tous_super_jobs:
        # 1. Normalisation HSJ
        # On transforme "HSJ_PUI" ou "HSJ_STERIL" en "HUB_HSJ"
        orig_raw = sj['jobs'][0].origin
        dest_raw = sj['jobs'][0].destination
        
        orig = "HUB_HSJ" if "HSJ_" in orig_raw.upper() else orig_raw
        dest = "HUB_HSJ" if "HSJ_" in dest_raw.upper() else dest_raw
        
        # On ignore les mouvements internes au HUB HSJ (si existants)
        if orig == dest:
            continue

        # 2. Création de la clé du couloir (non-ordonnée pour grouper A->B et B->A)
        # Un frozenset({A, B}) est identique à frozenset({B, A})
        nom_couloir = frozenset([orig, dest])
        
        if nom_couloir not in couloirs:
            # On initialise le couloir avec les deux sens possibles
            # On utilise un tuple ordonné pour les clés internes pour savoir qui est qui
            site_liste = list(nom_couloir)
            s1, s2 = site_liste[0], site_liste[1]
            couloirs[nom_couloir] = {
                f"{s1}_vers_{s2}": [],
                f"{s2}_vers_{s1}": []
            }
        
        # 3. Rangement du Super Job dans le bon sens
        sens = f"{orig}_vers_{dest}"
        couloirs[nom_couloir][sens].append(sj)

    return couloirs




"""
Répartit les jobs par couloir selon leur représentativité :
- Groupes (fenêtres identiques) : Étalement uniforme sur la fenêtre.
- Solitaires : Positionnement strict au plus tôt (h_dispo_max).
"""
def etaler_uniformément_par_couloir(couloirs):
    for nom_couloir, sens_dict in couloirs.items():
        for sens, liste_sj in sens_dict.items():
            groupes_fenetres = {}
            for sj in liste_sj:
                # CORRECTION : Calcul de la deadline de départ
                h_max_depart_secu = sj['h_deadline_min'] - sj['poids_total']
                h_max_depart = max(sj['h_dispo_max'], h_max_depart_secu)
                
                cle = (sj['h_dispo_max'], h_max_depart)
                if cle not in groupes_fenetres: groupes_fenetres[cle] = []
                groupes_fenetres[cle].append(sj)
            
            for (h_min, h_max), jobs in groupes_fenetres.items():
                nb_jobs = len(jobs)
                if nb_jobs == 1:
                    jobs[0]['h_depart_actuelle'] = h_min
                else:
                    if h_max > h_min:
                        intervalle = (h_max - h_min) / (nb_jobs - 1)
                        for idx, sj in enumerate(jobs):
                            sj['h_depart_actuelle'] = h_min + (idx * intervalle)
                    else:
                        for sj in jobs: sj['h_depart_actuelle'] = h_min
    return couloirs




"""
Lissage marginal dynamique (on ajuste la charge pour le véhicule au cours de la journée. 
"""
def ajustement_marginal_dynamique(couloirs):

    if "params_logistique" not in st.session_state:
        st.error("Configuration logistique manquante.")
        return couloirs
    
    params = st.session_state["params_logistique"]
    capacites_flotte = st.session_state.get("resultat_lissage_flotte", {})
    
    h_debut_explo = to_decimal_minutes(params["rh"]["h_prise_min"])
    h_fin_explo = to_decimal_minutes(params["rh"]["h_fin_max"])
    
    # 1. Mise à plat de tous les jobs pour analyser la charge globale par type de véhicule
    tous_les_jobs = []
    for sens_dict in couloirs.values():
        for liste_sj in sens_dict.values():
            tous_les_jobs.extend(liste_sj)
    
    types_v = set(j['jobs'][0].vehicule_type for j in tous_les_jobs)
    
    for v_type in types_v:
        capa_camions = capacites_flotte.get(v_type.upper(), 1)
        # Capacité en minutes-camion pour un créneau de 30 min
        CAPA_TEMPS_CRENEAU = capa_camions * 30 
        
        jobs_v = [j for j in tous_les_jobs if j['jobs'][0].vehicule_type == v_type]
        
        # On définit des créneaux de 30 min
        creneaux = list(range(int(h_debut_explo), int(h_fin_explo), 30))
        
        for c in creneaux:
            # Identifier les jobs qui occupent ce créneau
            jobs_actifs = []
            poids_occupe = 0
            
            for j in jobs_v:
                h_dep = j['h_depart_actuelle']
                h_fin = h_dep + j['poids_total']
                
                # Si le job chevauche le créneau [c, c + 30]
                if h_dep < c + 30 and h_fin > c:
                    intersection = min(h_fin, c + 30) - max(h_dep, c)
                    poids_occupe += intersection
                    jobs_actifs.append(j)

            # 2. Si surcharge, on tente de décaler
            if poids_occupe > CAPA_TEMPS_CRENEAU:
                # On trie : ceux qui ont le plus de marge (slack) en premier
                # Marge = Deadline - (Heure Fin Actuelle)
                jobs_actifs.sort(key=lambda x: (x['h_deadline_min'] - (x['h_depart_actuelle'] + x['poids_total'])), reverse=True)
                
                for sj in jobs_actifs:
                    if poids_occupe <= CAPA_TEMPS_CRENEAU: 
                        break
                    
                    nouveau_dep = sj['h_depart_actuelle'] + 15
                    
                    # --- CONDITION DE SÉCURITÉ CRITIQUE ---
                    # Le nouveau départ est autorisé SI ET SEULEMENT SI :
                    # 1. Il finit avant sa deadline
                    # 2. Il finit avant la fin de l'exploitation globale
                    if nouveau_dep + sj['poids_total'] <= min(sj['h_deadline_min'], h_fin_explo):
                        sj['h_depart_actuelle'] = nouveau_dep
                        poids_occupe -= 15 
                    else:
                        # Si on ne peut pas décaler ce job sans briser la deadline, 
                        # on le laisse là. L'ordonnancement devra gérer la surcharge 
                        # en ajoutant un camion si nécessaire.
                        pass

    return couloirs



def traitement_flux_recurrents(df_sequence_type, df_sites, df_vehicules, df_contenants, matrice_duree):
    """
    Orchestre la simulation en résolvant chaque sous-problème (type de véhicule) 
    indépendamment pour obtenir un dimensionnement précis par type.
    """
    st.info("🚀 Démarrage du traitement segmenté par type de véhicule...")

    if not isinstance(matrice_duree.index, pd.Index) or isinstance(matrice_duree.index, pd.RangeIndex):
        matrice_duree = matrice_duree.set_index(matrice_duree.columns[0])
        
    # 1. Éclater les flux par type de véhicule
    sous_problemes = eclater_flux_par_vehicule(df_sequence_type, df_sites, df_vehicules, df_contenants)
    
    # Récupération des paramètres
    params = st.session_state["params_logistique"]
    h_start = params["rh"]["h_prise_min"]
    h_end = params["rh"]["h_fin_max"]

    tous_les_postes_finaux = []
    dimensionnement_total = {}

    # 2. Boucle de traitement INDÉPENDANTE par type de véhicule
    for v_type, df_v in sous_problemes.items():
        v_type_str = str(v_type).upper()
        
        with st.status(f"🚛 Calcul du segment : {v_type_str}...", expanded=False) as status:
            # A. Fragmentation
            jobs_c, jobs_i = fragmenter_flux_en_jobs(df_v, h_start, h_end)
            
            # B. Appairage
            super_jobs_i = appairer_tous_les_jobs_incomplets(jobs_i, df_vehicules, df_contenants, matrice_duree)
            
            # C. Fusion
            liste_sj_v = preparer_liste_tous_super_jobs(jobs_c, super_jobs_i, matrice_duree)
            
            # D. Cartographie (spécifique au type en cours)
            couloirs_v = cartographier_couloirs(liste_sj_v)
            
            # E. Lissage temporel
            couloirs_v = etaler_uniformément_par_couloir(couloirs_v)
            couloirs_v = ajustement_marginal_dynamique(couloirs_v)
            
            # --- ÉTAPE CLÉ : ORDONNANCEMENT DU SOUS-PROBLÈME ---
            # On ne passe que les couloirs de CE véhicule à l'ordonnanceur
            res_segment = ordonnancer_flotte_optimale(couloirs_v, matrice_duree, v_type_str)
            
            if res_segment and res_segment["succes"]:
                n_camions = res_segment["n_camions"]
                postes_v = res_segment["postes"]
                
                # On stocke les résultats
                dimensionnement_total[v_type_str] = n_camions
                tous_les_postes_finaux.extend(postes_v)
                
                st.write(f"✅ **{v_type_str}** : {n_camions} véhicules, {len(postes_v)} chauffeurs.")
                status.update(label=f"✅ {v_type_str} : {n_camions} camions", state="complete")
            else:
                st.error(f"❌ Échec de l'ordonnancement pour le type {v_type_str}")
                status.update(label=f"❌ Erreur sur {v_type_str}", state="error")

    # 3. Synthèse finale
    if tous_les_postes_finaux:
        st.write("---")
        st.subheader("🏁 Résumé du dimensionnement")
        cols = st.columns(len(dimensionnement_total))
        for i, (name, count) in enumerate(dimensionnement_total.items()):
            cols[i].metric(name, f"{count} Camions")
        
        st.success(f"🎯 Simulation terminée. Total : {len(tous_les_postes_finaux)} postes chauffeurs créés.")
        return tous_les_postes_finaux
    else:
        st.error("❌ Aucun ordonnancement n'a pu être trouvé pour l'ensemble de la flotte.")
        return []

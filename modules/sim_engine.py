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
        
        # NOMS EXACTS (Pour la recherche dans la matrice durée/distance)
        self.origin = str(origin).strip().upper()
        self.destination = str(destination).upper()
        
        # SITES DE REGROUPEMENT (Pour l'appairage/fusion des camions)
        # On considère que tous les sites HSJ sont au même point géographique
        self.origin_group = "HUB_HSJ" if "HSJ" in self.origin else self.origin
        self.dest_group = "HUB_HSJ" if "HSJ" in self.destination else self.destination
        
        self.h_dispo = h_dispo        
        self.h_deadline = h_deadline  
        self.quantite = quantite
        self.contenant = contenant
        self.vehicule_type = vehicule_type
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
    Boucle de décrémentation pour trouver le nombre minimal de chauffeurs.
    """
    if "params_logistique" not in st.session_state:
        return None

    params = st.session_state["params_logistique"]
    h_prise_min = to_decimal_minutes(params["rh"]["h_prise_min"])
    h_fin_max = to_decimal_minutes(params["rh"]["h_fin_max"])
    duree_poste_max = params["rh"]["duree_poste_max_minutes"]
    t_prepa = params["rh"]["temps_prepa_vehicule"]
    t_fin_poste = params["rh"]["temps_fin_poste"]
    depot = params["stationnement_initial"].upper()

    # Mise à plat de tous les Super Jobs lissés
    tous_les_jobs = []
    for sens_dict in couloirs.values():
        for liste_sj in sens_dict.values():
            tous_les_jobs.extend(liste_sj)
    tous_les_jobs.sort(key=lambda x: x['h_depart_actuelle'])

    # Estimation N_max (base de départ)
    n_max = sum(st.session_state.get("resultat_lissage_flotte", {"GLOBAL": 10}).values())
    
    solution_optimale = None

    # Test itératif de N camions à 1 camion
    for n_test in range(n_max, 0, -1):
        res = tenter_sequencage(
            n_test, tous_les_jobs, depot, matrice_duree, 
            h_prise_min, h_fin_max, duree_poste_max, t_prepa, t_fin_poste
        )
        
        if res["succes"]:
            solution_optimale = res
        else:
            break # On a trouvé la limite inférieure
            
    return solution_optimale

def tenter_sequencage(n_vehicules, jobs_a_faire, depot, matrice_duree, h_start, h_limite, max_poste, t_prepa, t_fin):
    postes = []
    for i in range(n_vehicules):
        postes.append({
            'id_chauffeur': f"CH_{i+1:02d}",
            'pos': depot,
            'h_dispo': h_start + t_prepa,
            'missions': [],
            'dernier_type_sanitaire': None,
            'pause_faite': False,
            'total_nettoyages': 0,
            'amplitude': 0
        })

    jobs_copy = copy.deepcopy(jobs_a_faire)

    for sj in jobs_copy:
        meilleur_candidat = None
        score_min = float('inf')
        
        for p in postes:
            score, h_dep, net = calculer_score_opportuniste(p, sj, matrice_duree, t_fin) # t_fin sert de t_nettoyage
            
            h_fin_m = h_dep + sj['poids_total']
            site_arr = sj['jobs'][-1].destination.upper()
            t_retour = matrice_duree.loc[site_arr, depot]
            h_retour_depot = h_fin_m + t_retour + t_fin
            
            # Vérifications strictes
            if (h_retour_depot - h_start <= max_poste) and \
               (h_fin_m <= sj['h_deadline_min']) and \
               (h_retour_depot <= h_limite):
                
                if score < score_min:
                    score_min = score
                    meilleur_candidat = {
                        'p': p, 'h_dep': h_dep, 'h_fin': h_fin_m, 
                        'h_ret': h_retour_depot, 'nettoyage': net
                    }

        if meilleur_candidat:
            sel = meilleur_candidat
            p = sel['p']
            # Gestion pause (si trou > 45min)
            if not p['pause_faite'] and (sel['h_dep'] - p['h_dispo'] >= 45):
                p['pause_faite'] = True

            p['missions'].append({
                'sj': sj,
                'h_dep': sel['h_dep'],
                'h_fin': sel['h_fin'],
                'nettoyage_effectue': sel['nettoyage']
            })
            p['pos'] = sj['jobs'][-1].destination.upper()
            p['h_dispo'] = sel['h_fin']
            p['dernier_type_sanitaire'] = sj['jobs'][0].type_propre_sale
            p['amplitude'] = sel['h_ret'] - h_start
            if sel['nettoyage']: p['total_nettoyages'] += 1
        else:
            return {"succes": False}

    return {"succes": True, "postes": postes}



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
            # --- CORRECTION ICI ---
            # On vérifie si v_elu est une Series (ligne de DF) et on extrait la valeur
            if hasattr(v_elu, 'Types'):
                # Si c'est une Series Pandas, on prend la valeur brute
                nom_v = v_elu['Types']
                if hasattr(nom_v, 'values'): # Cas où c'est encore une Series
                    nom_v = nom_v.values[0]
                
                vehicules_cibles.append(str(nom_v).strip().upper())
            else:
                vehicules_cibles.append(str(v_elu).strip().upper())
                
            capacites_max.append(capa)
        else:
            vehicules_cibles.append("INCONNU")
            capacites_max.append(0)

    df_travail['vehicule_cible'] = vehicules_cibles
    df_travail['capa_max_vehicule'] = capacites_max
    
    # Nettoyage final pour s'assurer qu'aucune Series n'a survécu
    df_travail['vehicule_cible'] = df_travail['vehicule_cible'].astype(str)
    
    sous_problemes = {
        v_type: df_travail[df_travail['vehicule_cible'] == v_type].copy()
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
    v_type = str(job_pivot.vehicule_type).strip().upper()
    vehicule = df_vehicules[df_vehicules['Types'].str.strip().upper() == v_type].iloc[0]
    
    # On regroupe par SITE GEOGRAPHIQUE (dest_group)
    candidats = [j for j in pool_candidats if 
                 j.dest_group == job_pivot.dest_group and 
                 j.type_propre_sale == job_pivot.type_propre_sale and
                 str(j.vehicule_type).upper() == v_type]
    
    meilleure_comb = []
    # Recherche avec le NOM EXACT dans la matrice
    poids_min = matrice_duree.loc[job_pivot.origin, job_pivot.destination]
    
    comb_test = [job_pivot]
    for c in candidats:
        if len(comb_test) >= 3: break
        if verifier_bin_packing_mixte(vehicule, comb_test + [c], df_contenants):
            comb_test.append(c)
            # Calcul du trajet multi-points avec noms exacts
            points_dep = list(set([j.origin for j in comb_test]))
            poids_test = 0
            curr = points_dep[0]
            for next_pt in points_dep[1:]:
                poids_test += matrice_duree.loc[curr, next_pt] + 10 
                curr = next_pt
            poids_test += matrice_duree.loc[curr, job_pivot.destination]
            
            meilleure_comb = comb_test[1:] 
            poids_min = poids_test
            
    return meilleure_comb, poids_min




"""
Cherche à grouper des jobs partant du même quai vers des destinations différentes (Tournée).
Ordonne les destinations par proximité pour minimiser les kilomètres et respecte la compatibilité sanitaire.
"""
def trouver_meilleure_comb_dep(job_pivot, pool_candidats, df_vehicules, df_contenants, matrice_duree):
    v_type = str(job_pivot.vehicule_type).strip().upper()
    vehicule = df_vehicules[df_vehicules['Types'].str.strip().upper() == v_type].iloc[0]
    
    # On regroupe par SITE GEOGRAPHIQUE (origin_group)
    candidats = [j for j in pool_candidats if 
                 j.origin_group == job_pivot.origin_group and 
                 j.type_propre_sale == job_pivot.type_propre_sale and
                 str(j.vehicule_type).upper() == v_type]
    
    meilleure_comb = []
    comb_test = [job_pivot]
    for c in candidats:
        if len(comb_test) >= 3: break
        if verifier_bin_packing_mixte(vehicule, comb_test + [c], df_contenants):
            comb_test.append(c)
            
            dests_uniques = list(set([j.destination for j in comb_test]))
            poids_test = 0
            curr = job_pivot.origin
            temp_dests = dests_uniques.copy()
            while temp_dests:
                # Recherche avec NOM EXACT
                proche = min(temp_dests, key=lambda d: matrice_duree.loc[curr, d])
                poids_test += matrice_duree.loc[curr, proche] + 10 
                curr = proche
                temp_dests.remove(proche)
            
            meilleure_comb = comb_test[1:]
            poids_min = poids_test
            
    if not meilleure_comb:
        poids_min = matrice_duree.loc[job_pivot.origin, job_pivot.destination]
        
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
    liste_globale = []
    
    # 1. On transforme les jobs complets en Super Jobs
    for j in jobs_complets:
        poids = matrice_duree.loc[j.origin, j.destination] + 10 # Trajet + manoeuvre
        liste_globale.append({
            'id_super_job': f"SJ_C_{j.job_id}",
            'jobs': [j],
            'poids_total': poids,
            'type_combinaison': 'COMPLET',
            'h_dispo_max': j.h_dispo,
            'h_deadline_min': j.h_deadline
        })
        
    # 2. On ajoute les super jobs issus des incomplets
    liste_globale.extend(super_jobs_incomplets)
    
    # 3. Tri chronologique global
    liste_globale.sort(key=lambda x: x['h_dispo_max'])
    
    return liste_globale




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
            # 1. Groupement par fenêtre identique (h_min, h_max)
            groupes_fenetres = {}
            for sj in liste_sj:
                h_max_depart = sj['h_deadline_min'] - sj['poids_total']
                cle = (sj['h_dispo_max'], h_max_depart)
                if cle not in groupes_fenetres:
                    groupes_fenetres[cle] = []
                groupes_fenetres[cle].append(sj)
            
            # 2. Application des deux régimes de positionnement
            for (h_min, h_max), jobs in groupes_fenetres.items():
                nb_jobs = len(jobs)
                
                if nb_jobs == 1:
                    # OPTION 3 : Job solitaire -> Au plus tôt
                    jobs[0]['h_depart_actuelle'] = h_min
                
                else:
                    # ÉTALEMENT : Répartition uniforme sur la fenêtre disponible
                    # On utilise toute l'amplitude de h_min à h_max
                    if h_max > h_min:
                        intervalle = (h_max - h_min) / (nb_jobs - 1)
                        for idx, sj in enumerate(jobs):
                            sj['h_depart_actuelle'] = h_min + (idx * intervalle)
                    else:
                        # Si h_min == h_max, pas de choix, tout au même moment
                        for sj in jobs:
                            sj['h_depart_actuelle'] = h_min
                            
    return couloirs




"""
Lissage marginal dynamique (on ajuste la charge pour le véhicule au cours de la journée. 
"""
def ajustement_marginal_dynamique(couloirs):
    # 1. Récupération des capacités et des bornes temporelles
    if "params_logistique" not in st.session_state:
        st.error("Configuration logistique manquante.")
        return couloirs
    
    params = st.session_state["params_logistique"]
    capacites_flotte = st.session_state.get("resultat_lissage_flotte", {})
    
    # Extraction dynamique des bornes (conversion en minutes)
    h_debut_explo = to_decimal_minutes(params["rh"]["h_prise_min"])
    h_fin_explo = to_decimal_minutes(params["rh"]["h_fin_max"])
    
    # 2. Création de la liste des créneaux de 30 min entre ces deux bornes
    creneaux = list(range(int(h_debut_explo), int(h_fin_explo), 30))
    
    # 3. Mise à plat et filtrage par véhicule
    tous_les_jobs = []
    for sens_dict in couloirs.values():
        for liste_sj in sens_dict.values():
            tous_les_jobs.extend(liste_sj)
    
    types_v = set(j['jobs'][0].vehicule_type for j in tous_les_jobs)
    
    for v_type in types_v:
        # On récupère la capacité spécifique calculée pour ce type
        capa_camions = capacites_flotte.get(v_type.upper(), 1)
        CAPA_TEMPS_CRENEAU = capa_camions * 30 
        
        jobs_v = [j for j in tous_les_jobs if j['jobs'][0].vehicule_type == v_type]
        
        for c in creneaux:
            # Calcul du poids total sur le créneau [c, c + 30]
            jobs_actifs = []
            poids_occupe = 0
            
            for j in jobs_v:
                h_dep = j['h_depart_actuelle']
                h_fin = h_dep + j['poids_total']
                
                if h_dep < c + 30 and h_fin > c:
                    occ = min(h_fin, c + 30) - max(h_dep, c)
                    poids_occupe += occ
                    jobs_actifs.append(j)

            # 4. Lissage si dépassement de la capacité temps
            if poids_occupe > CAPA_TEMPS_CRENEAU:
                # Tri par marge de manoeuvre décroissante
                jobs_actifs.sort(key=lambda x: (x['h_deadline_min'] - (x['h_depart_actuelle'] + x['poids_total'])), reverse=True)
                
                for sj in jobs_actifs:
                    if poids_occupe <= CAPA_TEMPS_CRENEAU: break
                    
                    nouveau_dep = sj['h_depart_actuelle'] + 15
                    # On vérifie que le décalage ne dépasse pas la deadline ET la fin d'exploitation
                    if nouveau_dep + sj['poids_total'] <= min(sj['h_deadline_min'], h_fin_explo):
                        sj['h_depart_actuelle'] = nouveau_dep
                        poids_occupe -= 15 

    return couloirs



def traitement_flux_recurrents(df_sequence_type, df_sites, df_vehicules, df_contenants, matrice_duree):
    """
    Orchestre la simulation complète avec affichage du suivi pour le débogage.
    """
    st.info("🚀 Démarrage du traitement des flux récurrents...")

    # 1. Éclater les flux par type de véhicule
    sous_problemes = eclater_flux_par_vehicule(df_sequence_type, df_sites, df_vehicules, df_contenants)
    st.write(f"✅ Flux éclatés en {len(sous_problemes)} types de véhicules.", sous_problemes.keys())
    
    # Récupération des paramètres
    params = st.session_state["params_logistique"]
    h_start = params["rh"]["h_prise_min"]
    h_end = params["rh"]["h_fin_max"]

    tous_les_couloirs_fusionnes = {}

    # 2. Boucle de traitement par type de véhicule
    for v_type, df_v in sous_problemes.items():
        with st.expander(f"🔍 Détails débogage - {v_type}", expanded=False):
            # A. Fragmentation
            jobs_c, jobs_i = fragmenter_flux_en_jobs(df_v, h_start, h_end)
            st.write(f"📦 Fragmentation : {len(jobs_c)} jobs complets, {len(jobs_i)} jobs incomplets.")
            
            # B. Appairage
            super_jobs_i = appairer_tous_les_jobs_incomplets(jobs_i, df_vehicules, df_contenants, matrice_duree)
            st.write(f"🤝 Appairage : {len(super_jobs_i)} Super Jobs créés à partir des incomplets.")
            if super_jobs_i:
                st.write("Exemple 1er Super Job (Incomplet) :", super_jobs_i[0])
            
            # C. Fusion
            liste_sj_v = preparer_liste_tous_super_jobs(jobs_c, super_jobs_i, matrice_duree)
            st.write(f"📊 Total Super Jobs pour {v_type} : {len(liste_sj_v)}")
            
            # D. Cartographie
            couloirs_v = cartographier_couloirs(liste_sj_v)
            st.write(f"🗺️ Nombre de couloirs géographiques : {len(couloirs_v)}")
            
            # E. Lissage temporel
            couloirs_v = etaler_uniformément_par_couloir(couloirs_v)
            couloirs_v = ajustement_marginal_dynamique(couloirs_v)
            st.write("⏳ Lissage et ajustement marginal terminés.")
            
            tous_les_couloirs_fusionnes.update(couloirs_v)

    # 3. Ordonnancement final
    st.write("🔃 Lancement de l'ordonnancement de la flotte (calcul des tournées)...")
    resultat_final = ordonnancer_flotte_optimale(tous_les_couloirs_fusionnes, matrice_duree)

    if resultat_final and resultat_final["succes"]:
        postes = resultat_final["postes"]
        st.success(f"🎯 Ordonnancement réussi ! {len(postes)} chauffeurs mobilisés.")
        
        # Visualisation rapide du premier poste pour vérification
        if len(postes) > 0:
            with st.expander("👀 Visualiser le planning du Chauffeur 01 (Débogage)", expanded=False):
                st.write(postes[0])
        
        return postes
    else:
        st.error("❌ L'ordonnancement a échoué. Vérifiez les contraintes de temps ou le nombre de véhicules.")
        return []

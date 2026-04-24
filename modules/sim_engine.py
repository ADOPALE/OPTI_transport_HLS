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
    """
    Représente une unité de transport élémentaire.
    Inclut la logique de Hub pour HSJ et la gestion des tournées imposées.
    """
    def __init__(self, job_id, flux_id, type_job, origin, destination, 
                 h_dispo, h_deadline, quantite, contenant, 
                 vehicule_type, type_propre_sale, aller_retour,
                 tournee_rattachement=None, taux_occupation=0):
        
        self.job_id = job_id          
        self.flux_id = flux_id        
        self.type_job = type_job      # 'COMPLET' ou 'INCOMPLET'
        
        # --- GÉOGRAPHIE & LOGIQUE DE HUB ---
        self.origin = str(origin).strip().upper()
        self.destination = str(destination).strip().upper()
        
        # Logique de regroupement (Macro-sites)
        self.origin_group = "HUB_HSJ" if "HSJ_" in self.origin else self.origin
        self.dest_group = "HUB_HSJ" if "HSJ_" in self.destination else self.destination
        
        # --- PROTECTION VÉHICULE ---
        if isinstance(vehicule_type, (pd.Series, list)):
            self.vehicule_type = str(vehicule_type[0]).strip().upper() if len(vehicule_type) > 0 else "INCONNU"
        else:
            self.vehicule_type = str(vehicule_type).strip().upper()

        # --- TEMPS & QUANTITÉS ---
        self.h_dispo = h_dispo        
        self.h_deadline = h_deadline  
        self.quantite = quantite
        self.contenant = str(contenant).strip().upper()
        self.type_propre_sale = type_propre_sale
        self.aller_retour = aller_retour 

        # --- ATTRIBUTS DE PHASE 3 (COMBINAISON) ---
        self.tournee_rattachement = tournee_rattachement
        self.taux_occupation = taux_occupation
        
        # --- ATTRIBUTS DE PHASE 4 (ORDONNANCEMENT) ---
        self.est_planifie = False
        self.poids_temps = 0 

    def __repr__(self):
        # Affichage enrichi pour le debug
        return (f"Job({self.job_id} | {self.origin_group}->{self.dest_group} | "
                f"Qte:{self.quantite} {self.contenant} | Occ:{round(self.taux_occupation,2)})")    






class SuperJob:
    """
    Représente un camion chargé. 
    Peut contenir un ou plusieurs Jobs (complets ou partiels).
    """
    def __init__(self, liste_jobs, matrice_duree):
        self.liste_jobs = liste_jobs
        self.matrice_duree = matrice_duree
        
        # 1. Calcul de l'occupation totale
        self.taux_occupation_total = sum(j.taux_occupation for j in liste_jobs)
        
        # 2. Identification des points uniques (pour le trajet)
        self.points_depart = list(set(j.origin for j in liste_jobs))
        self.points_arrivee = list(set(j.destination for j in liste_jobs))
        
        # 3. Récupération des contraintes de temps les plus strictes
        self.h_dispo_max = max(j.h_dispo for j in liste_jobs)  # Dispo quand le DERNIER est prêt
        self.h_deadline_min = min(j.h_deadline for j in liste_jobs) # Livré avant le PREMIER deadline
        
        # Tag pour le suivi (optionnel)
        self.nom_tournee_origine = getattr(liste_jobs[0], 'tournee_rattachement', None)

    def calculer_poids_mobilisation(self):
        """
        Calcule le temps total de mobilisation du camion (en minutes).
        Logique : Temps de trajet entre tous les points + Temps de manutention.
        """
        params = st.session_state["params_logistique"]
        tps_quai = params.get("temps_changement_quai", 15) # Temps fixe par arrêt supp
        
        poids_total = 0
        itineraire = []
        
        # --- LOGIQUE SIMPLIFIÉE DE TRAJET ---
        # On part du principe que le camion fait : 
        # Départs (un par un) -> Arrivées (une par une)
        
        tous_les_points = self.points_depart + self.points_arrivee
        nb_arrets = len(set(tous_les_points))
        
        # 1. Calcul du temps de trajet (somme des segments successifs)
        # Note : Dans l'étape de séquençage, on optimisera l'ordre exact.
        # Ici, on fait une estimation pour l'arbitrage.
        for i in range(len(tous_les_points) - 1):
            p1 = tous_les_points[i]
            p2 = tous_les_points[i+1]
            poids_total += self.matrice_duree.get((p1, p2), 0)
            
        # 2. Ajout du temps de manutention (Changement de quai)
        # On paye le temps de quai pour chaque arrêt après le premier
        poids_total += (nb_arrets - 1) * tps_quai
        
        return poids_total

    def peut_ajouter(self, job, taux_max_cible):
        """ Vérifie si un job peut être ajouté sans dépasser la limite cible. """
        return (self.taux_occupation_total + job.taux_occupation) <= (taux_max_cible + 0.0001)

    def ajouter_job(self, job):
        """ Ajoute un job et recalcule les propriétés. """
        self.liste_jobs.append(job)
        self.taux_occupation_total += job.taux_occupation
        self.points_depart = list(set(j.origin for j in self.liste_jobs))
        self.points_arrivee = list(set(j.destination for j in self.liste_jobs))

    def __repr__(self):
        return f"SuperJob({len(self.liste_jobs)} jobs, Occ:{round(self.taux_occupation_total,2)})"

# =================================================================
# FONCTIONS DE CALCUL SANITAIRE ET OPPORTUNISTE
# =================================================================

def preparer_flux_complets_du_jour(df_recurrent, df_specifique, jour_nom):
    """
    Fusionne les flux et harmonise les noms de colonnes.
    """
    # 1. On définit ce que le moteur de simulation attend ABSOLUMENT
    COL_CIBLE_TYPE = 'Type (propre/sale)'
    
    # 2. Préparation des Récurrents
    df_rec = df_recurrent.copy()
    
    # Si la colonne s'appelle 'Sale / propre', on la renomme pour le moteur
    if 'Sale / propre' in df_rec.columns:
        df_rec = df_rec.rename(columns={'Sale / propre': COL_CIBLE_TYPE})
        
    df_rec['Quantite_du_jour'] = df_rec['Quantité_Séquence_Type']
    df_rec['Origine_Flux'] = 'RECURRENT'
    
    # 3. Préparation des Spécifiques
    df_spec = pd.DataFrame()
    if df_specifique is not None and not df_specifique.empty:
        # Recherche de la colonne de quantité pour le jour J
        col_quantite = next((c for c in df_specifique.columns if jour_nom.lower() in c.lower() and ("quant" in c.lower() or "qt" in c.lower())), None)
        
        if col_quantite:
            df_spec = df_specifique[df_specifique[col_quantite] > 0].copy()
            df_spec['Quantite_du_jour'] = df_spec[col_quantite]
            df_spec['Origine_Flux'] = 'SPECIFIQUE'
            
            # Harmonisation pour les spécifiques aussi
            if 'Sale / propre' in df_spec.columns:
                df_spec = df_spec.rename(columns={'Sale / propre': COL_CIBLE_TYPE})
            
            # On aligne les autres colonnes critiques au cas où
            mapping_autres = {
                'Heure de livraison': 'Heure max de livraison à la destination',
                'Heure de dispo': 'Heure de mise à disposition min départ'
            }
            df_spec = df_spec.rename(columns=mapping_autres)

    # 4. Fusion finale sécurisée
    if not df_spec.empty:
        # On ne garde que les colonnes communes pour éviter les NaN polluants
        colonnes_communes = [c for c in df_rec.columns if c in df_spec.columns]
        return pd.concat([df_rec[colonnes_communes], df_spec[colonnes_communes]], ignore_index=True)
    
    return df_rec



def Eclater_par_vehicule(df_complet_jour, df_vehicules, df_contenants, df_sites):
    """
    Etape 2 : Utilise les fonctions métiers pour affecter le meilleur véhicule 
    et calculer la capacité utile réelle.
    """
    # 1. RÉCUPÉRATION DES PARAMÈTRES
    params = st.session_state.get("params_logistique", {})
    vehicules_autorises = params.get("vehicules_selectionnes", [])
    taux_remplissage = params.get("securite_remplissage", 1.0)
    
    # Filtrage de la flotte sur celle sélectionnée par l'utilisateur
    col_nom_v = df_vehicules.columns[0]
    df_v_actifs = df_vehicules[df_vehicules[col_nom_v].isin(vehicules_autorises)].copy()

    # Nettoyage des noms de colonnes sites (comme dans tes fonctions)
    df_sites.columns = [str(c).strip().upper() for c in df_sites.columns]
    col_libelle = next((c for c in df_sites.columns if "LIBEL" in c or "SITE" in c), None)

    resultats = []

    for index, flux in df_complet_jour.iterrows():
        site_dep = str(flux['Point de départ']).strip().upper()
        site_arr = str(flux['Point de destination']).strip().upper()
        type_cont = str(flux['Nature de contenant']).strip().upper()

        # 2. APPEL À TA FONCTION : identifier_meilleur_vehicule
        # Elle vérifie l'accessibilité (est_accessible) et cherche la meilleure capacité au sol
        v_elu, capa_max_theorique = identifier_meilleur_vehicule(
            site_dep, 
            site_arr, 
            type_cont, 
            df_v_actifs, 
            df_contenants, 
            df_sites, 
            col_libelle
        )

        if v_elu is not None and capa_max_theorique > 0:
            # 3. CALCUL DE LA CAPACITÉ UTILE (avec ton taux de remplissage)
            # On applique le floor car on ne transporte pas de fractions de contenants
            capa_utile = math.floor(capa_max_theorique * taux_remplissage)
            
            # Sécurité : au moins 1 si le véhicule est compatible
            capa_utile = max(1, capa_utile)
            
            v_nom = v_elu[col_nom_v]
        else:
            v_nom = "NON_COMPATIBLE_OU_PAS_SELECTIONNE"
            capa_utile = 0

        # Mise à jour de la ligne
        flux_maj = flux.to_dict()
        flux_maj['Vehicule_Affecte'] = v_nom
        flux_maj['Capa_Max_Transport'] = capa_utile
        resultats.append(flux_maj)

    return pd.DataFrame(resultats)


def fragmenter_en_jobs(df_eclate_v, v_type_str, df_vehicules, df_contenants):
    """
    Eclate chaque ligne de flux en N jobs complets + 1 job incomplet (résiduel).
    Calcule le taux d'occupation réel par rapport à la capacité technique max.
    """
    jobs_complets = []
    jobs_incomplets = []
    
    # On récupère le nom de la colonne des types dans le référentiel véhicule
    col_nom_v = df_vehicules.columns[0]

    for index, row in df_eclate_v.iterrows():
        qte_totale = row['Quantite_du_jour']
        capa_u = row['Capa_Max_Transport'] # Capacité avec taux de remplissage (ex: 28)
        
        # --- GESTION DES ERREURS CRITIQUES ---
        
        # Cas 1 : Quantité positive mais capacité nulle (Incompatibilité véhicule/contenant)
        if qte_totale > 0 and capa_u <= 0:
            st.error(f"### ❌ Erreur de Capacité : {v_type_str}")
            st.warning(f"""
            **Flux impossible à charger :**
            - **Origine :** {row['Point de départ']}
            - **Destination :** {row['Point de destination']}
            - **Contenant :** {row['Nature de contenant']}
            
            **Cause probable :** Le véhicule sélectionné ne peut techniquement pas transporter ce contenant.
            """)
            st.stop()

        # Cas 2 : Quantité nulle ou négative (Erreur de données source)
        if qte_totale <= 0:
            # On ignore les lignes à 0 sans bloquer, 
            # SAUF si tu considères que c'est une anomalie de ton fichier.
            # Ici, on continue simplement pour ne pas créer de jobs vides.
            continue

        # --- CALCUL DU TAUX D'OCCUPATION RÉEL ---
        # On récupère la capacité max théorique (100%) pour le calcul du taux physique
        try:
            v_info = df_vehicules[df_vehicules[col_nom_v] == v_type_str].iloc[0]
            c_info = df_contenants[df_contenants['libellé'] == row['Nature de contenant']].iloc[0]
            capa_theorique_100 = calculer_capacite_max(v_info, c_info)
        except:
            capa_theorique_100 = capa_u # Backup si erreur de recherche

        # 1. Création des jobs complets (au sens "complet selon taux paramétré")
        nb_pleins = int(qte_totale // capa_u)
        for i in range(nb_pleins):
            job_c = Job(
                job_id=f"{row['Origine_Flux']}_{index}_C{i}",
                flux_id=index,
                type_job='COMPLET',
                origin=row['Point de départ'],
                destination=row['Point de destination'],
                h_dispo=to_decimal_minutes(row['Heure de mise à disposition min départ']),
                h_deadline=to_decimal_minutes(row['Heure max de livraison à la destination']),
                quantite=capa_u,
                contenant=row['Nature de contenant'],
                vehicule_type=v_type_str,
                type_propre_sale=row['Type (propre/sale)'],
                aller_retour=row['Aller/Retour'],
                tournee_rattachement=row.get('Nom de la tournée mutualisée le cas échéant'),
                # Taux d'occupation physique réel
                taux_occupation=capa_u / capa_theorique_100 if capa_theorique_100 > 0 else 1.0
            )
            jobs_complets.append(job_c)
            
        # 2. Création du job incomplet (le résiduel)
        reste = qte_totale % capa_u
        if reste > 0:
            job_i = Job(
                job_id=f"{row['Origine_Flux']}_{index}_R",
                flux_id=index,
                type_job='INCOMPLET',
                origin=row['Point de départ'],
                destination=row['Point de destination'],
                h_dispo=to_decimal_minutes(row['Heure de mise à disposition min départ']),
                h_deadline=to_decimal_minutes(row['Heure max de livraison à la destination']),
                quantite=reste,
                contenant=row['Nature de contenant'],
                vehicule_type=v_type_str,
                type_propre_sale=row['Type (propre/sale)'],
                aller_retour=row['Aller/Retour'],
                tournee_rattachement=row.get('Nom de la tournée mutualisée le cas échéant'),
                # Taux d'occupation physique réel du reste
                taux_occupation=reste / capa_theorique_100 if capa_theorique_100 > 0 else reste/capa_u
            )
            jobs_incomplets.append(job_i)
            
    return jobs_complets, jobs_incomplets


def regrouper_tournees_imposees(jobs_incomplets, matrice_duree):
    """
    ÉTAPE 1 : Regroupement des jobs partiels ayant une tournée de rattachement commune.
    Respecte strictement la limite du TAUX D'OCCUPATION MAX paramétré.
    """
    # Récupération directe (supposée existante car enregistrée au préalable)
    params = st.session_state["params_logistique"]
    taux_max_cible = params["securite_remplissage"]
    
    super_jobs_tournees = []
    jobs_solitaires = []
    
    # 1. Tri : Séparation entre tournées imposées et jobs libres
    groupes_tournees = {}
    
    for j in jobs_incomplets:
        nom_t = j.tournee_rattachement
        # On vérifie si une tournée est spécifiée dans le Excel
        if pd.notna(nom_t) and str(nom_t).strip() != "":
            nom_t = str(nom_t).strip().upper()
            if nom_t not in groupes_tournees:
                groupes_tournees[nom_t] = []
            groupes_tournees[nom_t].append(j)
        else:
            # Si pas de nom de tournée, le job part dans la pile des "solitaires"
            jobs_solitaires.append(j)

    # 2. Création des SuperJobs pour chaque groupe de tournée
    for nom_t, liste_j in groupes_tournees.items():
        # On trie par taux d'occupation décroissant pour remplir au mieux l'enveloppe cible
        liste_j.sort(key=lambda x: x.taux_occupation, reverse=True)
        
        current_sj_list = []
        cumul_occ = 0
        
        for job in liste_j:
            # Vérification par rapport au taux de sécurité utilisateur (ex: 0.8)
            # On ajoute une petite tolérance flottante (0.0001) pour éviter les arrondis capricieux
            if cumul_occ + job.taux_occupation <= (taux_max_cible + 0.0001):
                current_sj_list.append(job)
                cumul_occ += job.taux_occupation
            else:
                # La limite de gestion est atteinte : on clôture ce camion
                new_sj = SuperJob(current_sj_list, matrice_duree)
                new_sj.nom_tournee_origine = nom_t 
                super_jobs_tournees.append(new_sj)
                
                # On ouvre le camion suivant de la même tournée avec le job actuel
                current_sj_list = [job]
                cumul_occ = job.taux_occupation
        
        # On ferme le dernier camion du groupe
        if current_sj_list:
            last_sj = SuperJob(current_sj_list, matrice_duree)
            last_sj.nom_tournee_origine = nom_t
            super_jobs_tournees.append(last_sj)
            
    return super_jobs_tournees, jobs_solitaires


def preparer_pile_optimisation(super_jobs_tournees, jobs_solitaires_initiaux):
    """
    Décide quels jobs vont être envoyés à l'arbitrage (Etape 2).
    """
    params = st.session_state["params_logistique"]
    autoriser_melange = params.get("optimiser_reliquats_tournees", True)

    super_jobs_scelles = []
    pile_a_optimiser = jobs_solitaires_initiaux.copy()

    for sj in super_jobs_tournees:
        # On considère qu'un SuperJob est un "reliquat" s'il est sous le taux_max_cible
        # (Chaque SuperJob doit avoir une propriété .taux_occupation_total)
        taux_max_cible = params["securite_remplissage"]
        
        if autoriser_melange and sj.taux_occupation_total < (taux_max_cible - 0.01):
            # On "casse" le SuperJob incomplet pour remettre ses jobs dans la pile d'optimisation
            pile_a_optimiser.extend(sj.liste_jobs)
        else:
            # On garde le SuperJob tel quel (soit parce qu'il est plein, soit parce qu'on ne mélange pas)
            super_jobs_scelles.append(sj)

    return super_jobs_scelles, pile_a_optimiser

def optimiser_combinaison_solitaires(jobs_solitaires, matrice_duree):
    """
    Arbitre entre les différentes stratégies de regroupement.
    Priorise le remplissage maximum pour éviter les camions vides.
    """
    params = st.session_state.get("params_logistique", {"securite_remplissage": 1.0})
    taux_max_cible = params["securite_remplissage"]
    seuil_remplissage_minimum = max(0, taux_max_cible - 0.20)
    
    super_jobs_optimises = []
    restants = jobs_solitaires.copy()

    # Tri par heure pour respecter la chronologie
    restants.sort(key=lambda x: x.h_dispo)

    while len(restants) > 0:
        job_pivot = restants.pop(0)
        
        # --- PRIORITÉ 1 : COUPLES PARFAITS (Même trajet exact) ---
        couples_parfaits = [
            j for j in restants 
            if j.origin == job_pivot.origin 
            and j.destination == job_pivot.destination
            and j.vehicule_type == job_pivot.vehicule_type
            and j.type_propre_sale == job_pivot.type_propre_sale
        ]
        
        if couples_parfaits:
            comb_directe = [job_pivot]
            occ_directe = job_pivot.taux_occupation
            for c in couples_parfaits:
                if occ_directe + c.taux_occupation <= taux_max_cible + 0.0001:
                    comb_directe.append(c)
                    occ_directe += c.taux_occupation
            
            # Si le regroupement direct est efficace (au dessus du seuil ou plusieurs jobs)
            if occ_directe >= seuil_remplissage_minimum or len(comb_directe) >= 3:
                sj_direct = SuperJob(comb_directe, matrice_duree)
                super_jobs_optimises.append(sj_direct)
                ids_elus = [j.job_id for j in comb_directe if j.job_id != job_pivot.job_id]
                restants = [j for j in restants if j.job_id not in ids_elus]
                continue 

        # --- PRIORITÉ 2 : ARBITRAGE COMPLEXE (Multi-Pick ou Multi-Drop) ---
        # On évalue les deux stratégies
        sj_dest, poids_dest, membres_dest = evaluer_strategie(
            job_pivot, restants, "dest_group", taux_max_cible, matrice_duree
        )
        sj_dep, poids_dep, membres_dep = evaluer_strategie(
            job_pivot, restants, "origin_group", taux_max_cible, matrice_duree
        )

        # Calcul des taux de remplissage réels
        occ_dest = sum(j.taux_occupation for j in membres_dest)
        occ_dep = sum(j.taux_occupation for j in membres_dep)

        # LOGIQUE D'ARBITRAGE :
        # On choisit d'abord celle qui remplit le plus le camion
        if occ_dest > occ_dep + 0.01:
            meilleur_sj, elus = sj_dest, membres_dest
        elif occ_dep > occ_dest + 0.01:
            meilleur_sj, elus = sj_dep, membres_dep
        else:
            # Si remplissage identique, on prend le moins cher en temps (poids)
            if poids_dest <= poids_dep:
                meilleur_sj, elus = sj_dest, membres_dest
            else:
                meilleur_sj, elus = sj_dep, membres_dep

        # Si l'arbitrage n'a rien donné de mieux qu'un camion seul
        if len(elus) <= 1:
            meilleur_sj = SuperJob([job_pivot], matrice_duree)
            elus = [job_pivot]

        super_jobs_optimises.append(meilleur_sj)
        
        # Nettoyage de la liste des restants
        ids_elus = [j.job_id for j in elus if j.job_id != job_pivot.job_id]
        restants = [j for j in restants if j.job_id not in ids_elus]

    return super_jobs_optimises


def evaluer_strategie(job_pivot, candidats_pool, attribut_groupe, taux_max, matrice):
    """
    Explore une stratégie de regroupement (par Origine ou par Destination).
    Optimisée pour maximiser le remplissage (vorace).
    """
    valeur_groupe = getattr(job_pivot, attribut_groupe)
    
    # 1. Filtrage des partenaires compatibles
    partenaires = [
        j for j in candidats_pool 
        if getattr(j, attribut_groupe) == valeur_groupe 
        and j.vehicule_type == job_pivot.vehicule_type
        and j.type_propre_sale == job_pivot.type_propre_sale
    ]
    
    # On trie les partenaires par taux d'occupation décroissant 
    # pour boucher les gros trous d'abord
    partenaires.sort(key=lambda x: x.taux_occupation, reverse=True)
    
    groupe_retenu = [job_pivot]
    cumul_occ = job_pivot.taux_occupation
    
    # Paramètres de voracité
    SEUIL_CIBLE = max(0, taux_max - 0.20)
    LIMITE_NB_JOBS = 15 # Augmenté pour permettre de grouper les petits flux (ex: prisons)

    for p in partenaires:
        # Si on a encore de la place physique
        if (cumul_occ + p.taux_occupation) <= (taux_max + 0.0001):
            # Si on n'a pas atteint la limite de stops raisonnable
            if len(groupe_retenu) < LIMITE_NB_JOBS:
                groupe_retenu.append(p)
                cumul_occ += p.taux_occupation
        
        # Si on est déjà "plein" (proche du taux max), on peut arrêter de chercher
        if cumul_occ >= taux_max - 0.01:
            break

    # 2. Création du SuperJob
    sj_temp = SuperJob(groupe_retenu, matrice)
    
    # 3. Calcul du Score (Poids de mobilisation)
    # On ajoute une pénalité de score si le camion est trop vide 
    # pour que l'étape d'arbitrage rejette cette option si une meilleure existe
    score = sj_temp.calculer_poids_mobilisation()
    if cumul_occ < SEUIL_CIBLE:
        # Pénalité proportionnelle au vide pour forcer l'algo à chercher d'autres voisins
        score += (SEUIL_CIBLE - cumul_occ) * 1000 
    
    return sj_temp, score, groupe_retenu


def convertir_complets_en_super_jobs(jobs_complets, matrice_duree):
    """
    Transforme chaque Job complet en un SuperJob unique.
    Cela permet d'unifier la suite du traitement (lissage, ordonnancement).
    """
    super_jobs_complets = []
    
    for j in jobs_complets:
        # On crée un SuperJob contenant un seul job
        sj = SuperJob([j], matrice_duree)
        
        # On lui donne un tag spécifique pour le suivi dans les logs
        sj.type_combinaison = "DIRECT_COMPLET"
        
        super_jobs_complets.append(sj)
        
    return super_jobs_complets

def tunnel_consolidation_flux(df_complet_jour, df_vehicules, df_contenants, df_sites, matrice_duree):
    """
    Transforme les flux bruts du jour en une liste de SuperJobs optimisés.
    """
    # ETAPE 1 : Choix du véhicule et calcul capacité utile
    df_eclate = Eclater_par_vehicule(df_complet_jour, df_vehicules, df_contenants, df_sites)
    
    tous_les_super_jobs_du_jour = []
    
    # On segmente par type de véhicule pour ne pas mélanger les capacités
    types_vehicules = df_eclate['Vehicule_Affecte'].unique()
    
    for v_type in types_vehicules:
        if v_type == "NON_COMPATIBLE_OU_PAS_SELECTIONNE":
            continue
            
        df_v = df_eclate[df_eclate['Vehicule_Affecte'] == v_type]
        
        # ETAPE 2 : Fragmentation (Complets vs Incomplets)
        jobs_c, jobs_i = fragmenter_en_jobs(df_v, v_type, df_vehicules, df_contenants)
        
        # ETAPE 3 : Conversion des complets en SuperJobs
        sj_complets = convertir_complets_en_super_jobs(jobs_c, matrice_duree)
        
        # ETAPE 4 : Gestion des tournées imposées
        sj_imposes, solitaires_initiaux = regrouper_tournees_imposees(jobs_i, matrice_duree)
        
        # ETAPE 5 : Préparation de la pile (Reliquats + Solitaires)
        sj_scelles, pile_a_optimiser = preparer_pile_optimisation(sj_imposes, solitaires_initiaux)
        
        # ETAPE 6 : Arbitrage et optimisation des solitaires
        sj_optimises = optimiser_combinaison_solitaires(pile_a_optimiser, matrice_duree)
        
        # Fusion pour ce type de véhicule
        tous_les_super_jobs_du_jour.extend(sj_complets)
        tous_les_super_jobs_du_jour.extend(sj_scelles)
        tous_les_super_jobs_du_jour.extend(sj_optimises)
        
    return tous_les_super_jobs_du_jour



























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

def ordonnancer_flotte_optimale(couloirs, matrice_duree, v_type):
    """
    Cherche le nombre minimal de véhicules pour un type spécifique.
    """
    if "params_logistique" not in st.session_state:
        return None

    params = st.session_state["params_logistique"]
    rh = params["rh"]

    h_prise_min = to_decimal_minutes(rh["h_prise_min"])
    h_fin_max = to_decimal_minutes(rh["h_fin_max"])
    duree_poste_max = rh["amplitude_totale"]
    t_prepa = rh["temps_fixes"] / 2  
    t_fin_poste = rh["temps_fixes"] / 2
    depot = params.get("stationnement_initial", "HLS").upper()

    # Mise à plat des jobs du segment
    tous_les_jobs = []
    for sens_dict in couloirs.values():
        for liste_sj in sens_dict.values():
            tous_les_jobs.extend(liste_sj)

    if not tous_les_jobs:
        return {"succes": True, "n_camions": 0, "postes": []}

    tous_les_jobs.sort(key=lambda x: x.get('h_depart_actuelle', 0))

    # Test itératif (de 1 à N jobs)
    for n_test in range(1, len(tous_les_jobs) + 1):
        res = tenter_sequencage(
            n_test, tous_les_jobs, depot, matrice_duree, 
            h_prise_min, h_fin_max, duree_poste_max, t_prepa, t_fin_poste, v_type
        )
        if res["succes"]:
            tous_les_postes = []
            for c in res['camions']:
                for p in c['postes']:
                    p['id_camion'] = c['id_camion']
                    p['v_type'] = v_type
                    tous_les_postes.append(p)
            return {"succes": True, "n_camions": n_test, "postes": tous_les_postes}

    return {"succes": False}

def tenter_sequencage(n_camions, jobs_a_faire, depot, matrice_duree, h_start, h_limite, max_poste, t_prepa, t_fin, v_type):
    """
    Tente d'attribuer les jobs à une flotte de n_camions pour un type donné.
    """
    camions = []
    for i in range(n_camions):
        camions.append({
            'id_camion': f"{v_type}_{i+1:02d}", # ID unique par type
            'type': v_type,
            'pos_actuelle': depot,
            'h_dispo_vehicule': h_start,
            'postes': []
        })

    # Tri par heure de départ pour la logique de remplissage
    jobs_copy = sorted(copy.deepcopy(jobs_a_faire), key=lambda x: x.get('h_depart_actuelle', 0))

    for sj in jobs_copy:
        attribue = False
        for c in camions:
            besoin_nouveau_p = False
            # Vérification si le chauffeur actuel peut prendre le job
            if not c['postes'] or c['postes'][-1]['fini']:
                besoin_nouveau_p = True
            else:
                p_act = c['postes'][-1]
                score, h_dep, net = calculer_score_opportuniste(p_act, sj, matrice_duree, t_fin)
                h_fin_m = h_dep + sj['poids_total']
                h_ret = h_fin_m + matrice_duree.loc[sj['jobs'][-1].destination.upper(), depot] + t_fin
                debut = p_act['h_debut_service'] if p_act['h_debut_service'] is not None else (h_dep - t_prepa)
                
                # Si le job dépasse l'amplitude ou l'heure de fin max -> Fin de poste
                if (h_ret - debut > max_poste) or (h_ret > h_limite):
                    p_act['fini'] = True
                    besoin_nouveau_p = True
            
            if besoin_nouveau_p:
                # Nouveau poste (relève de chauffeur) sur le même camion
                h_dispo_v = max(c['h_dispo_vehicule'], h_start)
                p_neuf = {
                    'id_chauffeur': f"{c['id_camion']}_CH_{len(c['postes'])+1}",
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
                
                # Vérification faisabilité pour le nouveau chauffeur
                if h_fin_m <= sj['h_deadline_min'] and (h_ret - (h_dep - t_prepa)) <= max_poste and h_ret <= h_limite:
                    p_neuf['h_debut_service'] = h_dep - t_prepa
                    c['postes'].append(p_neuf)
                    p_cible = c['postes'][-1]
                else:
                    continue
            else:
                p_cible = c['postes'][-1]

            # Attribution du job
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
    """
    Répartit les départs sur toute la plage disponible.
    Sécurité : Initialise h_depart_actuelle pour éviter les KeyError.
    """
    for key in couloirs:
        for sens in ["ALLER", "RETOUR"]:
            if sens not in couloirs[key]:
                continue
                
            jobs = couloirs[key][sens]
            if not jobs:
                continue
            
            # --- SÉCURITÉ : Initialisation systématique ---
            for sj in jobs:
                if 'h_depart_actuelle' not in sj:
                    sj['h_depart_actuelle'] = sj['h_dispo_max']
            
            # Trier par deadline pour le lissage
            jobs.sort(key=lambda x: x.get('h_deadline_min', 0))
            
            n = len(jobs)
            t_start = min(sj['h_dispo_max'] for sj in jobs)
            t_end = max(sj['h_deadline_min'] for sj in jobs)
            
            plage = max(30, t_end - t_start)
            pas = plage / n if n > 1 else 0

            for i, sj in enumerate(jobs):
                cible = t_start + (i * pas)
                h_depart = max(cible, sj['h_dispo_max'])
                
                # On ne dépasse pas la deadline
                if h_depart > sj['h_deadline_min']:
                    h_depart = sj['h_deadline_min']
                
                sj['h_depart_actuelle'] = h_depart
                
    return couloirs


"""
Lissage marginal dynamique (on ajuste la charge pour le véhicule au cours de la journée. 
"""
def ajustement_marginal_dynamique(couloirs):
    """
    Évite les collisions de départ par site. 
    Sécurité : utilise .get() pour éviter KeyError lors du tri.
    """
    tous_les_sj = []
    for key in couloirs:
        for sens in couloirs[key]:
            tous_les_sj.extend(couloirs[key][sens])
            
    if not tous_les_sj:
        return couloirs
            
    # SÉCURITÉ : On s'assure que chaque job a une heure, même si le lissage a échoué
    for sj in tous_les_sj:
        if 'h_depart_actuelle' not in sj:
            sj['h_depart_actuelle'] = sj['h_dispo_max']

    # 1. Tri chronologique sécurisé
    tous_les_sj.sort(key=lambda x: x.get('h_depart_actuelle', x['h_dispo_max']))
    
    # 2. Suivi des départs par site pour le cadencement (10 min)
    dernier_depart_par_site = {}
    CADENCE_MINUTAGE = 10 

    for sj in tous_les_sj:
        # On vérifie qu'il y a bien un job dans le super_job pour trouver l'origine
        if not sj['jobs']: continue
        
        site_origine = sj['jobs'][0].origin_group
        h_prevue = sj['h_depart_actuelle']
        
        if site_origine in dernier_depart_par_site:
            h_mini_possible = dernier_depart_par_site[site_origine] + CADENCE_MINUTAGE
            
            if h_prevue < h_mini_possible:
                # Décalage sans dépasser la deadline
                h_limite = sj.get('h_deadline_min', h_prevue + 60)
                sj['h_depart_actuelle'] = min(h_mini_possible, h_limite)
        
        dernier_depart_par_site[site_origine] = sj['h_depart_actuelle']

    return couloirs


def traitement_flux_recurrents(df_sequence_type, df_sites, df_vehicules, df_contenants, matrice_duree):
    st.info("🚀 Démarrage du traitement segmenté par type de véhicule...")

    if not isinstance(matrice_duree.index, pd.Index) or isinstance(matrice_duree.index, pd.RangeIndex):
        matrice_duree = matrice_duree.set_index(matrice_duree.columns[0])
        
    sous_problemes = eclater_flux_par_vehicule(df_sequence_type, df_sites, df_vehicules, df_contenants)
    
    params = st.session_state["params_logistique"]
    h_start = params["rh"]["h_prise_min"]
    h_end = params["rh"]["h_fin_max"]

    tous_les_postes_finaux = []
    dimensionnement_total = {}

    for v_type, df_v in sous_problemes.items():
        v_type_str = str(v_type).upper()
        
        with st.status(f"🚛 Calcul du segment : {v_type_str}...", expanded=False) as status:
            # 1. Préparation (Fragmentation, Appairage, Fusion, Cartographie)
            jobs_c, jobs_i = fragmenter_flux_en_jobs(df_v, h_start, h_end)
            super_jobs_i = appairer_tous_les_jobs_incomplets(jobs_i, df_vehicules, df_contenants, matrice_duree)
            liste_sj_v = preparer_liste_tous_super_jobs(jobs_c, super_jobs_i, matrice_duree)
            couloirs_v = cartographier_couloirs(liste_sj_v)
            
            # 2. Lissage
            couloirs_v = etaler_uniformément_par_couloir(couloirs_v)
            couloirs_v = ajustement_marginal_dynamique(couloirs_v)
            
            # 3. Ordonnancement du SOUS-PROBLÈME (Appel segmenté)
            res_segment = ordonnancer_flotte_optimale(couloirs_v, matrice_duree, v_type_str)
            
            if res_segment and res_segment["succes"]:
                dimensionnement_total[v_type_str] = res_segment["n_camions"]
                tous_les_postes_finaux.extend(res_segment["postes"])
                st.write(f"✅ **{v_type_str}** : {res_segment['n_camions']} véhicules.")
                status.update(label=f"✅ {v_type_str} terminé", state="complete")
            else:
                st.error(f"❌ Échec pour {v_type_str}")
                status.update(label=f"❌ Erreur {v_type_str}", state="error")

    # Synthèse finale
    if tous_les_postes_finaux:
        st.write("---")
        st.subheader("🏁 Résumé du dimensionnement")
        cols = st.columns(len(dimensionnement_total))
        for i, (name, count) in enumerate(dimensionnement_total.items()):
            cols[i].metric(name, f"{count} véhicules")
        return tous_les_postes_finaux
    
    return []

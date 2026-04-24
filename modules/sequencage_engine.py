import pandas as pd
import math
from datetime import time, datetime

# =================================================================
# 1. UTILITAIRES
# =================================================================

def to_decimal_minutes(t):
    if isinstance(t, (time, datetime)):
        return t.hour * 60 + t.minute
    return 0

# =================================================================
# 2. CLASSE POSTE CHAUFFEUR
# =================================================================

class PosteChauffeur:
    def __init__(self, id_poste, vehicule_type, site_initial, params_rh):
        self.id_poste = id_poste
        self.vehicule_type = vehicule_type
        self.stationnement_initial = site_initial
        self.position_actuelle = site_initial
        self.couloir_actuel = None
        self.etat = 'INACTIF'
        self.job_en_cours = None
        self.temps_restant_etat = 0
        self.temps_service_total = 0   
        self.is_pause_faite = False
        self.amplitude_max = params_rh.get('v_duree', 450)
        self.duree_pause = params_rh.get('v_pause', 45)
        self.t_manoeuvre = 10 
        self.historique = []

    def enregistrer_evenement(self, minute_actuelle, activite, job=None, details=""):
        destination = self.position_actuelle
        sj_id = "N/A"
        if job:
            sj_id = job.flux_id
            if activite in ['EN_TRAJET_PLEIN', 'EN_DECHARGEMENT']:
                destination = job.destination
            elif activite in ['EN_TRAJET_VIDE', 'EN_MANOEUVRE_QUAI']:
                destination = job.origin

        self.historique.append({
            "Poste": self.id_poste,
            "Type": self.vehicule_type,
            "Minute_Debut": minute_actuelle,
            "Heure_Debut": f"{int(minute_actuelle//60):02d}:{int(minute_actuelle%60):02d}",
            "Activite": activite,
            "Origine": self.position_actuelle,
            "Destination": destination,
            "SJ_ID": sj_id,
            "Details": details
        })

    def mettre_a_jour(self, pas_temps):
        if self.etat in ['INACTIF', 'FIN_POSTE']: return False
        self.temps_service_total += pas_temps
        if self.temps_restant_etat > 0:
            self.temps_restant_etat -= pas_temps
            if self.temps_restant_etat <= 0:
                self.temps_restant_etat = 0
                return True
            return False
        return True

    def verifier_besoin_pause(self):
        return not self.is_pause_faite and self.temps_service_total >= (self.amplitude_max / 2)

    def verifier_fin_service(self):
        return self.temps_service_total >= self.amplitude_max

    def est_disponible(self):
        return self.etat == 'DISPONIBLE' and self.temps_restant_etat == 0

# =================================================================
# 3. LOGIQUE DE SCORING ET SÉLECTION
# =================================================================

def calculer_score_stress(job, temps_actuel):
    duree_mission = job.poids_total 
    temps_restant = job.h_deadline_min - temps_actuel
    if temps_restant <= 0: return 999999
    ratio = duree_mission / temps_restant
    return ratio * (1 / max(0.001, (1.1 - ratio)))

def trouver_meilleur_job(poste, jobs_dispos, matrice_duree):
    candidats = [j for j in jobs_dispos if j.v_type == poste.vehicule_type]
    if not candidats: return None
    
    candidats.sort(key=lambda x: x.score_stress, reverse=True)
    top_candidats = candidats[:5]
    
    # 1. Même couloir + Sur place
    for j in top_candidats:
        if j.couloir == poste.couloir_actuel and j.origin == poste.position_actuelle:
            return j
    # 2. Hors couloir + Sur place
    for j in top_candidats:
        if j.origin == poste.position_actuelle:
            return j
    # 3. Proche voisin
    scored = []
    for idx, j in enumerate(top_candidats):
        dist = matrice_duree.get(poste.position_actuelle, {}).get(j.origin, 999)
        scored.append(((idx + (dist / 10)), j))
    scored.sort(key=lambda x: x[0])
    return scored[0][1]

# =================================================================
# 4. FONCTION DE SÉQUENÇAGE CORRIGÉE (ORCHESTRATEUR)
# =================================================================

def ordonnancer_journee(liste_sj, n_max_dict, df_vehicules, matrice_duree, params_logistique):
    """
    Simule le déroulement d'une journée de transport pour valider si le nombre
    de véhicules (n_max_dict) permet de traiter tous les SuperJobs sans retard.
    """
    pas = 5  # Résolution de la simulation en minutes
    
    # 1. Récupération des horaires limites RH
    rh_params = params_logistique.get('rh', {})
    h_prise = to_decimal_minutes(rh_params.get('h_prise_min', time(6, 0)))
    h_fin = to_decimal_minutes(rh_params.get('h_fin_max', time(21, 0)))
    
    # 2. Identification dynamique des colonnes du référentiel véhicules
    # On cherche la colonne Type (souvent la 1ère)
    col_type = 'Type' if 'Type' in df_vehicules.columns else df_vehicules.columns[0]
    
    # On cherche la colonne manœuvre (mise à quai) par mot-clé
    cols_man = [c for c in df_vehicules.columns if 'manœuvre' in c.lower()]
    col_manoeuvre = cols_man[0] if cols_man else None

    # 3. Initialisation des Postes (Binômes Véhicule/Chauffeur)
    postes = []
    for v_type, n_veh in n_max_dict.items():
        if n_veh <= 0: continue
        
        # Sécurité : On vérifie que le type existe dans le référentiel
        mask = df_vehicules[col_type].astype(str).str.strip() == str(v_type).strip()
        df_filtre = df_vehicules[mask]
        
        if df_filtre.empty:
            continue # On ignore les types non définis pour éviter le crash .iloc[0]
            
        row_v = df_filtre.iloc[0]
        site_depot = row_v.get('Stationnement initial', "DEPOT")
        t_man = row_v[col_manoeuvre] if col_manoeuvre else 10
        
        for i in range(1, n_veh + 1):
            p = PosteChauffeur(f"{v_type}_{i:02d}", v_type, site_depot, rh_params)
            p.t_manoeuvre = t_man
            postes.append(p)

    if not postes:
        return {"succes": False, "erreur": "Aucun véhicule configuré", "reliquat": len(liste_sj)}

    # 4. Préparation de la boucle de simulation
    jobs_restants = [j for j in liste_sj]
    heure_actuelle = h_prise

    # 5. BOUCLE TEMPORELLE (Minute par minute)
    while heure_actuelle <= h_fin:
        
        # A. Mise à jour des Postes (ceux qui sont occupés avancent dans leur tâche)
        for p in postes:
            if p.mettre_a_jour(pas):
                # Si p.mettre_a_jour renvoie True, c'est qu'une étape vient de se finir
                
                if p.etat == 'PRISE_POSTE':
                    p.etat = 'DISPONIBLE'
                    p.enregistrer_evenement(heure_actuelle, "DISPONIBLE", details="Prise de poste terminée")
                
                elif p.etat == 'EN_TRAJET_VIDE':
                    # Arrivé au site (soit origine job, soit dépôt)
                    if p.job_en_cours:
                        p.etat = 'EN_MANOEUVRE_QUAI'
                        p.temps_restant_etat = p.t_manoeuvre
                        p.position_actuelle = p.job_en_cours.origin
                        p.enregistrer_evenement(heure_actuelle, "EN_MANOEUVRE_QUAI", p.job_en_cours, "Mise à quai Chargement")
                    else:
                        # C'était un retour dépôt (fin ou pause)
                        p.position_actuelle = p.stationnement_initial
                        p.etat = 'DISPONIBLE' # Sera traité au prochain cycle d'affectation

                elif p.etat == 'EN_MANOEUVRE_QUAI':
                    if p.job_en_cours and p.position_actuelle == p.job_en_cours.origin:
                        # On a fini la manoeuvre de mise à quai pour CHARGER
                        p.etat = 'EN_CHARGEMENT'
                        p.temps_restant_etat = p.job_en_cours.temps_chargement
                        p.enregistrer_evenement(heure_actuelle, "EN_CHARGEMENT", p.job_en_cours)
                    else:
                        # On a fini la manoeuvre de mise à quai pour DECHARGER
                        p.etat = 'EN_DECHARGEMENT'
                        p.temps_restant_etat = p.job_en_cours.temps_dechargement
                        p.enregistrer_evenement(heure_actuelle, "EN_DECHARGEMENT", p.job_en_cours)

                elif p.etat == 'EN_CHARGEMENT':
                    # Chargement fini -> On part en trajet plein
                    p.etat = 'EN_TRAJET_PLEIN'
                    p.couloir_actuel = p.job_en_cours.couloir
                    dist = matrice_duree.get(p.job_en_cours.origin, {}).get(p.job_en_cours.destination, 30)
                    p.temps_restant_etat = dist
                    p.enregistrer_evenement(heure_actuelle, "EN_TRAJET_PLEIN", p.job_en_cours)

                elif p.etat == 'EN_TRAJET_PLEIN':
                    # Arrivé à destination -> Manoeuvre déchargement
                    p.etat = 'EN_MANOEUVRE_QUAI'
                    p.temps_restant_etat = p.t_manoeuvre
                    p.position_actuelle = p.job_en_cours.destination
                    p.enregistrer_evenement(heure_actuelle, "EN_MANOEUVRE_QUAI", p.job_en_cours, "Mise à quai Déchargement")

                elif p.etat == 'EN_DECHARGEMENT':
                    # MISSION TERMINÉE
                    if heure_actuelle > p.job_en_cours.h_deadline_min:
                        return {"succes": False, "erreur": f"Retard sur {p.job_en_cours.flux_id} ({heure_actuelle} > {p.job_en_cours.h_deadline_min})"}
                    
                    p.etat = 'DISPONIBLE'
                    p.job_en_cours = None
                    p.enregistrer_evenement(heure_actuelle, "DISPONIBLE", details="Mission accomplie")

                elif p.etat == 'EN_PAUSE':
                    p.etat = 'DISPONIBLE'
                    p.enregistrer_evenement(heure_actuelle, "DISPONIBLE", details="Fin de pause")

        # B. Phase d'Affectation (Recherche de travail pour les postes DISPONIBLES)
        jobs_dispos = [j for j in jobs_restants if j.h_dispo_min <= heure_actuelle]
        for j in jobs_dispos:
            j.score_stress = calculer_score_stress(j, heure_actuelle)

        for p in postes:
            # 1. Prise de poste initiale
            if p.etat == 'INACTIF':
                if any(j.v_type == p.vehicule_type for j in jobs_dispos):
                    p.etat = 'PRISE_POSTE'
                    p.temps_restant_etat = 15 # Préparation véhicule
                    p.enregistrer_evenement(heure_actuelle, "PRISE_POSTE")
            
            # 2. Attribution de mission ou gestion RH
            elif p.est_disponible():
                # Priorité RH : Fin de service ou Pause
                if p.verifier_fin_service() or p.verifier_besoin_pause():
                    if p.position_actuelle == p.stationnement_initial:
                        if p.verifier_fin_service():
                            p.etat = 'FIN_POSTE'
                            p.enregistrer_evenement(heure_actuelle, "FIN_POSTE")
                        else:
                            p.etat = 'EN_PAUSE'
                            p.temps_restant_etat = p.duree_pause
                            p.is_pause_faite = True
                            p.enregistrer_evenement(heure_actuelle, "EN_PAUSE")
                    else:
                        # On force le retour au dépôt
                        dist_depot = matrice_duree.get(p.position_actuelle, {}).get(p.stationnement_initial, 30)
                        p.etat = 'EN_TRAJET_VIDE'
                        p.temps_restant_etat = dist_depot
                        p.enregistrer_evenement(heure_actuelle, "EN_TRAJET_VIDE", details="Retour Dépôt (RH)")
                
                # Sinon : Recherche de job
                else:
                    job = trouver_meilleur_job(p, jobs_dispos, matrice_duree)
                    if job:
                        p.job_en_cours = job
                        jobs_restants.remove(job)
                        jobs_dispos.remove(job)
                        
                        dist_approche = matrice_duree.get(p.position_actuelle, {}).get(job.origin, 0)
                        if dist_approche > 0:
                            p.etat = 'EN_TRAJET_VIDE'
                            p.temps_restant_etat = dist_approche
                            p.enregistrer_evenement(heure_actuelle, "EN_TRAJET_VIDE", job)
                        else:
                            p.etat = 'EN_MANOEUVRE_QUAI'
                            p.temps_restant_etat = p.t_manoeuvre
                            p.enregistrer_evenement(heure_actuelle, "EN_MANOEUVRE_QUAI", job, "Chargement immédiat")

        heure_actuelle += pas

    # Fin de journée : succès si tous les jobs sont faits
    return {
        "succes": len(jobs_restants) == 0, 
        "postes": postes, 
        "reliquat": len(jobs_restants)
    }
# =================================================================
# 5. FONCTION D'ITÉRATION (LA MEILLEURE SOLUTION)
# =================================================================

def trouver_meilleure_configuration_journee(liste_sj, intensite_par_type, df_vehicules, matrice_duree, params_logistique):
    """
    Prend le Nmax théorique issu du dictionnaire d'intensités et cherche par itération 
    le nombre minimum de camions REELS nécessaires.
    """
    import streamlit as st
    
    # Calcul des Nmax cibles (Pic d'intensité + 20% marge)
    n_max_initial = { v_type: math.ceil(max(intensites) * 1.2) for v_type, intensites in intensite_par_type.items() }
    
    # On commence le test à partir de 1 véhicule par type, jusqu'à Nmax + 2 (sécurité)
    # Pour simplifier, on itère proportionnellement
    limit_max = max(n_max_initial.values()) + 3
    
    st.info(f"🔎 Recherche d'une solution de séquençage optimisée...")
    
    solution_retenue = None
    
    # On boucle sur le nombre total de véhicules (en respectant le ratio par type)
    # Ici, pour plus de simplicité, on va tester la config n_max_initial directement
    # Si elle échoue, on augmente. Si elle réussit, on essaie de diminuer.
    
    res = ordonnancer_journee(liste_sj, n_max_initial, df_vehicules, matrice_duree, params_logistique)
    
    if res["succes"]:
        # Tentative d'optimisation : peut-on faire avec MOINS ?
        solution_retenue = res
        # (Optionnel : boucle pour réduire I et trouver le vrai minimum)
        return solution_retenue
    else:
        # Si Nmax théorique échoue, on tente une augmentation marginale
        st.warning("⚠️ Le Nmax théorique est insuffisant. Tentative avec capacité augmentée (+1 véhicule/type)...")
        n_max_safe = {v: count + 1 for v, count in n_max_initial.items()}
        res_retry = ordonnancer_journee(liste_sj, n_max_safe, df_vehicules, matrice_duree, params_logistique)
        
        if res_retry["succes"]:
            return res_retry
        else:
            st.error(f"❌ Échec critique : Aucun séquençage possible pour cette journée (Reliquats : {res_retry['reliquat']})")
            return None

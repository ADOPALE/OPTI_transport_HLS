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
    pas = 5
    h_prise = to_decimal_minutes(params_logistique['rh'].get('h_prise_min', time(6,0)))
    h_fin = to_decimal_minutes(params_logistique['rh'].get('h_fin_max', time(21,0)))
    
    postes = []
    for v_type, n_veh in n_max_dict.items():
        row_v = df_vehicules[df_vehicules['Types'] == v_type].iloc[0]
        for i in range(1, n_veh + 1):
            p = PosteChauffeur(f"{v_type}_{i:02d}", v_type, row_v['Stationnement initial'], params_logistique['rh'])
            p.t_manoeuvre = row_v['Temps de mise à quai - manœuvre, contact/admin (minutes)']
            postes.append(p)

    jobs_restants = [j for j in liste_sj]
    heure_actuelle = h_prise

    while heure_actuelle <= h_fin:
        # A. Mise à jour des Postes
        for p in postes:
            if p.mettre_a_jour(pas):
                if p.etat == 'PRISE_POSTE': p.etat = 'DISPONIBLE'
                elif p.etat == 'EN_TRAJET_VIDE':
                    p.etat = 'EN_MANOEUVRE_QUAI'
                    p.temps_restant_etat = p.t_manoeuvre
                    p.position_actuelle = p.job_en_cours.origin
                    p.enregistrer_evenement(heure_actuelle, "EN_MANOEUVRE_QUAI", p.job_en_cours)
                elif p.etat == 'EN_MANOEUVRE_QUAI':
                    if p.job_en_cours and p.position_actuelle == p.job_en_cours.origin:
                        p.etat = 'EN_CHARGEMENT'
                        p.temps_restant_etat = p.job_en_cours.temps_chargement
                    else:
                        p.etat = 'EN_DECHARGEMENT'
                        p.temps_restant_etat = p.job_en_cours.temps_dechargement
                    p.enregistrer_evenement(p.etat, heure_actuelle, p.job_en_cours)
                elif p.etat == 'EN_CHARGEMENT':
                    p.etat = 'EN_TRAJET_PLEIN'
                    p.couloir_actuel = p.job_en_cours.couloir
                    p.temps_restant_etat = matrice_duree.get(p.job_en_cours.origin, {}).get(p.job_en_cours.destination, 30)
                    p.enregistrer_evenement(heure_actuelle, "EN_TRAJET_PLEIN", p.job_en_cours)
                elif p.etat == 'EN_TRAJET_PLEIN':
                    p.etat = 'EN_MANOEUVRE_QUAI'
                    p.temps_restant_etat = p.t_manoeuvre
                    p.position_actuelle = p.job_en_cours.destination
                    p.enregistrer_evenement(heure_actuelle, "EN_MANOEUVRE_QUAI", p.job_en_cours)
                elif p.etat == 'EN_DECHARGEMENT':
                    if heure_actuelle > p.job_en_cours.h_deadline_min:
                        return {"succes": False, "erreur": f"Retard sur {p.job_en_cours.flux_id}"}
                    p.etat = 'DISPONIBLE'
                    p.job_en_cours = None
                    p.enregistrer_evenement(heure_actuelle, "DISPONIBLE")
                elif p.etat == 'EN_PAUSE': p.etat = 'DISPONIBLE'

        # B. Affectation
        jobs_dispos = [j for j in jobs_restants if j.h_dispo_min <= heure_actuelle]
        for j in jobs_dispos: j.score_stress = calculer_score_stress(j, heure_actuelle)

        for p in postes:
            if p.etat == 'INACTIF' and any(j.v_type == p.vehicule_type for j in jobs_dispos):
                p.etat = 'PRISE_POSTE'; p.temps_restant_etat = 15
            elif p.est_disponible():
                if p.verifier_fin_service() or p.verifier_besoin_pause():
                    if p.position_actuelle == p.stationnement_initial:
                        if p.verifier_fin_service(): p.etat = 'FIN_POSTE'
                        else: p.etat = 'EN_PAUSE'; p.temps_restant_etat = p.duree_pause; p.is_pause_faite = True
                    else:
                        p.etat = 'EN_TRAJET_VIDE'
                        p.temps_restant_etat = matrice_duree.get(p.position_actuelle, {}).get(p.stationnement_initial, 30)
                else:
                    job = trouver_meilleur_job(p, jobs_dispos, matrice_duree)
                    if job:
                        p.job_en_cours = job
                        jobs_restants.remove(job); jobs_dispos.remove(job)
                        dist = matrice_duree.get(p.position_actuelle, {}).get(job.origin, 0)
                        p.etat = 'EN_TRAJET_VIDE' if dist > 0 else 'EN_MANOEUVRE_QUAI'
                        p.temps_restant_etat = dist if dist > 0 else p.t_manoeuvre
                        p.enregistrer_evenement(heure_actuelle, p.etat, job)
        heure_actuelle += pas

    return {"succes": len(jobs_restants) == 0, "postes": postes, "reliquat": len(jobs_restants)}

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

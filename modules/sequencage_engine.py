import pandas as pd
import math
import streamlit as st
from datetime import time, datetime, timedelta

# =================================================================
# 1. UTILITAIRES
# =================================================================

def to_min(t):
    """Convertit Time ou DateTime en minutes depuis minuit."""
    if isinstance(t, (time, datetime)):
        return t.hour * 60 + t.minute
    return float(t)

def get_couloir_id(sj):
    """Identifie l'axe du job (A<->B) pour la priorité de couloir."""
    if not sj.points_depart or not sj.points_arrivee:
        return "INCONNU"
    pts = sorted([sj.points_depart[0], sj.points_arrivee[-1]])
    return f"{pts[0]}--{pts[1]}"

def calculer_stress_dynamique(sj, minute_actuelle):
    """
    Calcule l'urgence d'un SuperJob.
    Plus le ratio (durée / temps restant) approche 1, plus le stress est haut.
    """
    temps_restant = sj.h_deadline_min - minute_actuelle
    if temps_restant <= 0:
        return 9999.0
    
    ratio = sj.poids_total / temps_restant
    # Facteur d'accélération : le stress devient exponentiel au-delà de 0.9
    return ratio * (1 / max(0.01, (1.05 - ratio)))

# =================================================================
# 2. CLASSE POSTE CHAUFFEUR
# =================================================================

class PosteChauffeur:
    def __init__(self, id_p, v_type, site_depot, params_rh):
        self.id_poste = id_p
        self.vehicule_type = v_type
        self.stationnement_initial = site_depot
        self.position_actuelle = site_depot
        
        self.etat = 'INACTIF' # INACTIF, PRISE_POSTE, DISPONIBLE, EN_TRAJET_VIDE, EN_MISSION, EN_PAUSE, RETOUR_DEPOT
        self.temps_restant_etat = 0
        self.job_en_cours = None
        self.couloir_actuel = None
        
        self.h_debut_service = None
        self.pause_faite = False
        self.historique = []
        
        # Paramètres RH
        self.amplitude_max = params_rh.get('amplitude_totale', 450)
        self.duree_pause = params_rh.get('pause', 45)

    def enregistrer(self, minute, activite, sj=None, details=""):
        self.historique.append({
            "Minute_Debut": minute,
            "Heure_Debut": f"{int(minute//60):02d}h{int(minute%60):02d}",
            "Activite": activite,
            "SJ_ID": sj.liste_jobs[0].flux_id if sj and sj.liste_jobs else "N/A",
            "Details": details
        })

# =================================================================
# 3. LOGIQUE DE SÉLECTION (ANTI-BLOCAGE)
# =================================================================

def selectionner_meilleur_job(p, dispos, minute, matrice_duree):
    """
    Filtre les jobs physiquement réalisables (Trajet + Mission < Deadline)
    puis applique les priorités de couloir et de proximité.
    """
    candidats_possibles = []
    
    for j in dispos:
        # Temps pour aller au départ du job
        dist_approche = matrice_duree.get(p.position_actuelle, {}).get(j.points_depart[0], 0)
        
        # Condition de survie : Heure actuelle + Approche + Durée Mission <= Heure limite
        if minute + dist_approche + j.poids_total <= j.h_deadline_min:
            j.stress_temp = calculer_stress_dynamique(j, minute)
            candidats_possibles.append(j)

    if not candidats_possibles:
        return None

    # Tri par stress décroissant
    candidats_possibles.sort(key=lambda x: x.stress_temp, reverse=True)
    top_candidates = candidats_possibles[:5]

    # Priorité 1 : Même couloir ET déjà sur place
    for j in top_candidates:
        if get_couloir_id(j) == p.couloir_actuel and j.points_depart[0] == p.position_actuelle:
            return j

    # Priorité 2 : Déjà sur place (n'importe quel couloir)
    for j in top_candidates:
        if j.points_depart[0] == p.position_actuelle:
            return j

    # Priorité 3 : Le plus urgent parmi les plus proches physiquement
    top_candidates.sort(key=lambda x: matrice_duree.get(p.position_actuelle, {}).get(x.points_depart[0], 999))
    return top_candidates[0]

# =================================================================
# 4. MOTEUR DE SIMULATION (PAS DE 5 MIN)
# =================================================================

def simuler_faisabilite(I, liste_sj_type, v_type, matrice_duree, params_logistique, df_vehicules):
    rh = params_logistique.get('rh', {})
    h_start = to_min(rh.get('h_prise_min', 360))
    h_end = to_min(rh.get('h_fin_max', 1260))
    pas = 5
    
    # Trouver le dépôt pour ce type de véhicule
    try:
        depot_initial = df_vehicules[df_vehicules['Types'] == v_type]['Stationnement initial'].iloc[0]
    except:
        depot_initial = "DEPOT_DEFAUT"

    postes = [PosteChauffeur(f"{v_type}_{i+1}", v_type, depot_initial, rh) for i in range(I)]
    jobs_restants = list(liste_sj_type)
    minute = h_start

    # --- PRÉ-CHECK : JOBS IMPOSSIBLES ---
    for j in jobs_restants:
        fenetre = j.h_deadline_min - j.h_dispo_min
        if j.poids_total > fenetre:
            fid = j.liste_jobs[0].flux_id if j.liste_jobs else "N/A"
            st.error(f"""
            **🚨 JOB IMPOSSIBLE : {fid}**
            * Durée : {j.poids_total} min | Fenêtre : {fenetre} min
            * Dispo : {int(j.h_dispo_min//60):02d}h{int(j.h_dispo_min%60):02d} 
            * Limite : {int(j.h_deadline_min//60):02d}h{int(j.h_deadline_min%60):02d}
            """)
            return None

    # --- BOUCLE TEMPORELLE ---
    while minute <= h_end:
        # A. Mise à jour des tâches en cours
        for p in postes:
            if p.temps_restant_etat > 0:
                p.temps_restant_etat -= pas
                continue
            
            if p.etat == 'PRISE_POSTE':
                p.etat = 'DISPONIBLE'
            elif p.etat == 'EN_TRAJET_VIDE':
                p.position_actuelle = p.job_en_cours.points_depart[0]
                p.etat = 'EN_MISSION'
                p.temps_restant_etat = p.job_en_cours.poids_total
                p.enregistrer(minute, "EN_MISSION", p.job_en_cours)
            elif p.etat == 'EN_MISSION':
                if minute > p.job_en_cours.h_deadline_min:
                    return None # ÉCHEC : Retard
                p.position_actuelle = p.job_en_cours.points_arrivee[-1]
                p.couloir_actuel = get_couloir_id(p.job_en_cours)
                p.etat = 'DISPONIBLE'
                p.job_en_cours = None
            elif p.etat == 'RETOUR_DEPOT':
                p.position_actuelle = p.stationnement_initial
                p.etat = 'EN_PAUSE'
                p.temps_restant_etat = p.duree_pause
                p.pause_faite = True
                p.enregistrer(minute, "EN_PAUSE")
            elif p.etat == 'EN_PAUSE':
                p.etat = 'DISPONIBLE'

        # B. Affectation
        dispos = [j for j in jobs_restants if j.h_dispo_min <= minute]
        for p in postes:
            if p.etat == 'INACTIF' and dispos:
                p.etat = 'PRISE_POSTE'; p.temps_restant_etat = 15
                p.h_debut_service = minute; p.enregistrer(minute, "PRISE_POSTE")
            
            elif p.etat == 'DISPONIBLE':
                # Gestion de la pause
                temps_travail = minute - (p.h_debut_service if p.h_debut_service else minute)
                if temps_travail >= (p.amplitude_max / 2) and not p.pause_faite:
                    if p.position_actuelle == p.stationnement_initial:
                        p.etat = 'EN_PAUSE'; p.temps_restant_etat = p.duree_pause
                        p.pause_faite = True; p.enregistrer(minute, "EN_PAUSE")
                    else:
                        dist_d = matrice_duree.get(p.position_actuelle, {}).get(p.stationnement_initial, 30)
                        p.etat = 'RETOUR_DEPOT'; p.temps_restant_etat = dist_d
                        p.enregistrer(minute, "EN_TRAJET_VIDE", details="Retour Dépôt (Pause)")
                    continue

                if not dispos: continue
                
                best_sj = selectionner_meilleur_job(p, dispos, minute, matrice_duree)
                if best_sj:
                    p.job_en_cours = best_sj
                    jobs_restants.remove(best_sj)
                    dispos.remove(best_sj)
                    dist = matrice_duree.get(p.position_actuelle, {}).get(best_sj.points_depart[0], 0)
                    if dist > 0:
                        p.etat = 'EN_TRAJET_VIDE'; p.temps_restant_etat = dist
                        p.enregistrer(minute, "EN_TRAJET_VIDE", best_sj)
                    else:
                        p.etat = 'EN_MISSION'; p.temps_restant_etat = best_sj.poids_total
                        p.enregistrer(minute, "EN_MISSION", best_sj)

        if not jobs_restants:
            return postes
        minute += pas

    return None

# =================================================================
# 5. FONCTION PRINCIPALE (BOUCLE I)
# =================================================================

def trouver_meilleure_configuration_journee(liste_sj, n_max_dict, df_vehicules, matrice_duree, params_logistique):
    postes_complets = []
    
    for v_type, val_max in n_max_dict.items():
        # Règle : Max lissé + 20% arrondi au sup
        if isinstance(val_max, list):
            n_max_calc = math.ceil(max(val_max) * 1.20)
        else:
            n_max_calc = math.ceil(val_max * 1.20)
            
        st.info(f"Analyse **{v_type}** : Recherche de la solution optimale (1 à {n_max_calc} véhicules)")
        
        jobs_v = [sj for sj in liste_sj if sj.v_type == v_type]
        if not jobs_v: continue
            
        solution_trouvee = False
        for I in range(1, n_max_calc + 1):
            resultat = simuler_faisabilite(I, jobs_v, v_type, matrice_duree, params_logistique, df_vehicules)
            if resultat:
                st.success(f"✅ **{v_type}** : Validé avec **{I}** véhicule(s).")
                postes_complets.extend(resultat)
                solution_trouvee = True
                break
        
        if not solution_trouvee:
            st.error(f"❌ **{v_type}** : Aucun planning valide trouvé avec {n_max_calc} véhicules. Vérifiez les jobs impossibles ci-dessus.")

    return {"succes": len(postes_complets) > 0, "postes": postes_complets}

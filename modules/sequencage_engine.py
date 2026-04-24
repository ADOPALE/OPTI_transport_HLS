import pandas as pd
import math
import streamlit as st
from datetime import time, datetime, timedelta

# =================================================================
# 1. UTILITAIRES
# =================================================================

def to_min(t):
    if isinstance(t, (time, datetime)):
        return t.hour * 60 + t.minute
    return float(t)

def get_couloir_id(sj):
    if not sj.points_depart or not sj.points_arrivee:
        return "INCONNU"
    pts = sorted([sj.points_depart[0], sj.points_arrivee[-1]])
    return f"{pts[0]}--{pts[1]}"

def calculer_stress_dynamique(sj, minute_actuelle):
    """Stress index basé sur la deadline du premier maillon du SuperJob."""
    h_deadline_critique = to_min(sj.liste_jobs[0].h_deadline)
    temps_restant = h_deadline_critique - minute_actuelle
    
    if temps_restant <= 0: return 9999.0
    
    # On compare la durée totale du bloc au temps restant pour le démarrer
    ratio = sj.poids_total / (temps_restant + 1)
    return ratio * (1 / max(0.01, (1.1 - ratio)))

# =================================================================
# 2. CLASSE POSTE CHAUFFEUR (Définie en haut pour éviter ImportError)
# =================================================================

class PosteChauffeur:
    def __init__(self, id_p, v_type, site_depot, params_rh):
        self.id_poste = id_p
        self.vehicule_type = v_type
        self.stationnement_initial = site_depot
        self.position_actuelle = site_depot
        
        self.etat = 'INACTIF' 
        self.temps_restant_etat = 0
        self.job_en_cours = None
        self.couloir_actuel = None
        
        self.h_debut_service = None
        self.pause_faite = False
        self.historique = []
        
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
# 3. LOGIQUE DE SÉLECTION (MAILLON CRITIQUE)
# =================================================================

def selectionner_meilleur_job(p, dispos, minute, matrice_duree):
    """
    Vérifie si on peut :
    1. Arriver au chargement du 1er job
    2. ET finir la livraison du 1er job avant sa deadline.
    """
    candidats_possibles = []
    
    for j in dispos:
        first_job = j.liste_jobs[0]
        h_deadline_1er = to_min(first_job.h_deadline)
        
        # 1. Temps d'approche vers l'origine du 1er Job
        dist_approche = matrice_duree.get(p.position_actuelle, {}).get(j.points_depart[0], 0)
        
        # 2. Temps du 1er segment (on prend le premier job du bloc)
        # Note : poids_total inclus manutention + trajet. On estime le maillon 1.
        duree_maillon_1 = first_job.poids_total if hasattr(first_job, 'poids_total') else (j.poids_total / len(j.liste_jobs))
        
        # CONDITION CRITIQUE : Heure Actuelle + Approche + Premier Segment <= Deadline du Premier Job
        if minute + dist_approche + duree_maillon_1 <= h_deadline_1er:
            j.stress_temp = calculer_stress_dynamique(j, minute)
            candidats_possibles.append(j)

    if not candidats_possibles:
        return None

    # Tri par stress, puis priorités métiers
    candidats_possibles.sort(key=lambda x: x.stress_temp, reverse=True)
    top_3 = candidats_possibles[:3]

    for j in top_3:
        if get_couloir_id(j) == p.couloir_actuel and j.points_depart[0] == p.position_actuelle:
            return j
    for j in top_3:
        if j.points_depart[0] == p.position_actuelle:
            return j
            
    top_3.sort(key=lambda x: matrice_duree.get(p.position_actuelle, {}).get(x.points_depart[0], 999))
    return top_3[0]

# =================================================================
# 4. MOTEUR DE SIMULATION
# =================================================================

def simuler_faisabilite(I, liste_sj_type, v_type, matrice_duree, params_logistique, df_vehicules):
    rh = params_logistique.get('rh', {})
    h_start = to_min(rh.get('h_prise_min', 360))
    h_end = to_min(rh.get('h_fin_max', 1260))
    pas = 5
    
    try:
        depot_initial = df_vehicules[df_vehicules['Types'] == v_type]['Stationnement initial'].iloc[0]
    except:
        depot_initial = "DEPOT"

    postes = [PosteChauffeur(f"{v_type}_{i+1}", v_type, depot_initial, rh) for i in range(I)]
    jobs_restants = list(liste_sj_type)
    minute = h_start

    while minute <= h_end:
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
                # Fin de mission
                p.position_actuelle = p.job_en_cours.points_arrivee[-1]
                p.couloir_actuel = get_couloir_id(p.job_en_cours)
                p.etat = 'DISPONIBLE'
                p.job_en_cours = None
            elif p.etat == 'RETOUR_DEPOT':
                p.position_actuelle = p.stationnement_initial
                p.etat = 'EN_PAUSE'; p.temps_restant_etat = p.duree_pause
                p.pause_faite = True; p.enregistrer(minute, "EN_PAUSE")
            elif p.etat == 'EN_PAUSE':
                p.etat = 'DISPONIBLE'

        # Affectation
        dispos = [j for j in jobs_restants if to_min(j.liste_jobs[0].h_dispo) <= minute]
        for p in postes:
            if p.etat == 'INACTIF' and dispos:
                p.etat = 'PRISE_POSTE'; p.temps_restant_etat = 15
                p.h_debut_service = minute; p.enregistrer(minute, "PRISE_POSTE")
            
            elif p.etat == 'DISPONIBLE':
                # Gestion de la pause au dépôt
                if (minute - (p.h_debut_service or minute)) >= (p.amplitude_max / 2) and not p.pause_faite:
                    if p.position_actuelle == p.stationnement_initial:
                        p.etat = 'EN_PAUSE'; p.temps_restant_etat = p.duree_pause; p.pause_faite = True
                        p.enregistrer(minute, "EN_PAUSE")
                    else:
                        dist_d = matrice_duree.get(p.position_actuelle, {}).get(p.stationnement_initial, 30)
                        p.etat = 'RETOUR_DEPOT'; p.temps_restant_etat = dist_d
                        p.enregistrer(minute, "EN_TRAJET_VIDE", details="Retour Dépôt (Pause)")
                    continue

                if not dispos: continue
                
                best_sj = selectionner_meilleur_job(p, dispos, minute, matrice_duree)
                if best_sj:
                    dist = matrice_duree.get(p.position_actuelle, {}).get(best_sj.points_depart[0], 0)
                    p.job_en_cours = best_sj
                    jobs_restants.remove(best_sj)
                    dispos.remove(best_sj)
                    
                    if dist > 0:
                        p.etat = 'EN_TRAJET_VIDE'; p.temps_restant_etat = dist
                        p.enregistrer(minute, "EN_TRAJET_VIDE", best_sj)
                    else:
                        p.etat = 'EN_MISSION'; p.temps_restant_etat = best_sj.poids_total
                        p.enregistrer(minute, "EN_MISSION", best_sj)

        if not jobs_restants: return postes
        minute += pas

    return None

# =================================================================
# 5. FONCTION D'ENTREE
# =================================================================

def trouver_meilleure_configuration_journee(liste_sj, n_max_dict, df_vehicules, matrice_duree, params_logistique):
    postes_complets = []
    for v_type, val_max in n_max_dict.items():
        # Règle : Max théorique + 20%
        n_max_calc = math.ceil(max(val_max) * 2) if isinstance(val_max, list) else math.ceil(val_max * 2)
        st.info(f"Analyse **{v_type}** : Recherche d'optimisation (Max: {n_max_calc} camions)")
        
        jobs_v = [sj for sj in liste_sj if sj.v_type == v_type]
        if not jobs_v: continue
            
        solution_trouvee = False
        for I in range(1, n_max_calc + 1):
            res = simuler_faisabilite(I, jobs_v, v_type, matrice_duree, params_logistique, df_vehicules)
            if res:
                st.success(f"✅ **{v_type}** : Solution validée avec **{I}** véhicule(s).")
                postes_complets.extend(res)
                solution_trouvee = True
                break
        
        if not solution_trouvee:
            st.error(f"❌ **{v_type}** : Aucun planning valide trouvé. Les délais du 1er segment sont peut-être trop courts.")

    return {"succes": len(postes_complets) > 0, "postes": postes_complets}

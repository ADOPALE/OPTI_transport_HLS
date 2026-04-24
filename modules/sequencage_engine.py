import pandas as pd
import math
import streamlit as st  # <--- BIEN VÉRIFIER QUE C'EST TOUT EN HAUT
from datetime import time, datetime, timedelta

# =================================================================
# 1. UTILITAIRES
# =================================================================

def to_min(t):
    if isinstance(t, (time, datetime)): return t.hour * 60 + t.minute
    return float(t)

def calculer_stress_dynamique(sj, minute_actuelle):
    """
    Stress = Temps_de_trajet_requis / Temps_restant_avant_deadline
    """
    temps_restant = sj.h_deadline_min - minute_actuelle
    if temps_restant <= 0: return 9999.0 # Urgentissime ou retard
    
    ratio = sj.poids_total / temps_restant
    # Le facteur explose quand on approche de la limite
    return ratio * (1 / max(0.01, (1.1 - ratio)))

def get_couloir_id(sj):
    """Identifie le couloir unique A<->B"""
    pts = sorted([sj.points_depart[0], sj.points_arrivee[-1]])
    return f"{pts[0]}--{pts[1]}"

# =================================================================
# 2. CLASSE POSTE
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
        
        # Paramètres
        self.amplitude_max = params_rh.get('v_duree', 450)
        self.duree_pause = params_rh.get('v_pause', 45)

    def enregistrer(self, minute, activite, sj=None, details=""):
        self.historique.append({
            "Minute_Debut": minute,
            "Heure_Debut": f"{int(minute//60):02d}:{int(minute%60):02d}",
            "Activite": activite,
            "SJ_ID": sj.liste_jobs[0].flux_id if sj else "N/A",
            "Details": details
        })

# =================================================================
# 3. MOTEUR DE SIMULATION (PAS DE 5 MIN)
# =================================================================

def simuler_faisabilite(I, liste_sj_type, v_type, matrice_duree, params_logistique):
    rh = params_logistique.get('rh', {})
    h_start = to_min(rh.get('h_prise_min', 360))
    h_end = to_min(rh.get('h_fin_max', 1260))
    pas = 5
    
    # On récupère le dépôt du premier camion de ce type trouvé (ou par défaut)
    # Note : à adapter selon ton df_vehicules
    depot_initial = "DEPOT" 
    
    postes = [PosteChauffeur(f"{v_type}_{i+1}", v_type, depot_initial, rh) for i in range(I)]
    jobs_restants = list(liste_sj_type)
    minute = h_start

    while minute <= h_end:
        # A. Mise à jour des postes
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
                    return None # ÉCHEC : Retard détecté
                p.position_actuelle = p.job_en_cours.points_arrivee[-1]
                p.couloir_actuel = get_couloir_id(p.job_en_cours)
                p.etat = 'DISPONIBLE'
                p.job_en_cours = None
            elif p.etat == 'RETOUR_PAUSE':
                p.position_actuelle = p.stationnement_initial
                p.etat = 'EN_PAUSE'
                p.temps_restant_etat = p.duree_pause
                p.pause_faite = True
                p.enregistrer(minute, "EN_PAUSE")
            elif p.etat == 'EN_PAUSE':
                p.etat = 'DISPONIBLE'

        # B. Attribution des jobs
        dispos = [j for j in jobs_restants if j.h_dispo_min <= minute]
        for j in dispos: j.stress = calculer_stress_dynamique(j, minute)
        dispos.sort(key=lambda x: x.stress, reverse=True)

        for p in postes:
            if p.etat == 'INACTIF' and dispos:
                p.etat = 'PRISE_POSTE'
                p.temps_restant_etat = 15
                p.h_debut_service = minute
                p.enregistrer(minute, "PRISE_POSTE")
            
            elif p.etat == 'DISPONIBLE':
                temps_travail = minute - p.h_debut_service
                
                # Gestion de la pause au dépôt (mi-parcours)
                if temps_travail >= (p.amplitude_max / 2) and not p.pause_faite:
                    if p.position_actuelle == p.stationnement_initial:
                        p.etat = 'EN_PAUSE'; p.temps_restant_etat = p.duree_pause
                        p.pause_faite = True; p.enregistrer(minute, "EN_PAUSE")
                    else:
                        dist = matrice_duree.get(p.position_actuelle, {}).get(p.stationnement_initial, 30)
                        p.etat = 'RETOUR_PAUSE'; p.temps_restant_etat = dist
                        p.enregistrer(minute, "EN_TRAJET_VIDE", details="Retour Dépôt pour Pause")
                    continue

                if not dispos: continue

                # Sélection du meilleur job (Top 5 stress)
                top_5 = dispos[:5]
                best_sj = None
                
                # 1. Même couloir + Sur place
                for j in top_5:
                    if get_couloir_id(j) == p.couloir_actuel and j.points_depart[0] == p.position_actuelle:
                        best_sj = j; break
                # 2. Sur place
                if not best_sj:
                    for j in top_5:
                        if j.points_depart[0] == p.position_actuelle:
                            best_sj = j; break
                # 3. Plus proche voisin
                if not best_sj:
                    top_5.sort(key=lambda x: matrice_duree.get(p.position_actuelle, {}).get(x.points_depart[0], 999))
                    best_sj = top_5[0]

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

        if not jobs_restants: return postes
        minute += pas

    return None

# =================================================================
# 4. FONCTION D'ENTRÉE (BOUCLE I)
# =================================================================

def trouver_meilleure_configuration_journee(liste_sj, n_max_dict, df_vehicules, matrice_duree, params_logistique):
    """
    Parcourt I de 1 à Nmax pour chaque type de véhicule.
    """
    postes_finaux = []
    
    for v_type, n_max in n_max_dict.items():
        st.info(f"Analyse du type {v_type} (Max dispo: {int(n_max)})")
        
        jobs_type = [sj for sj in liste_sj if sj.v_type == v_type]
        if not jobs_type: continue
        
        solution_trouvee = False
        for I in range(1, int(n_max) + 1):
            res = simuler_faisabilite(I, jobs_type, v_type, matrice_duree, params_logistique)
            if res:
                postes_finaux.extend(res)
                st.success(f"✅ {v_type} : Solution validée avec {I} véhicule(s).")
                solution_trouvee = True
                break
        
        if not solution_trouvee:
            st.error(f"❌ {v_type} : Impossible de livrer tous les jobs avec {int(n_max)} véhicules.")

    return {"succes": True, "postes": postes_finaux}

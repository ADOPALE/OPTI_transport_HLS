import pandas as pd
import math
from datetime import time, datetime, timedelta
import streamlit as st


# =================================================================
# 1. UTILITAIRES DE CALCUL
# =================================================================

def to_min(t):
    if isinstance(t, (time, datetime)): return t.hour * 60 + t.minute
    return float(t)

def calculer_stress(sj, minute_actuelle):
    """
    Stress = Temps_de_trajet_requis / Temps_restant_avant_deadline
    Plus le score est élevé, plus c'est urgent. 
    Si temps_restant < poids_total, le score explose (> 1.0).
    """
    temps_restant = sj.h_deadline_min - minute_actuelle
    if temps_restant <= 0: return 999.0
    
    ratio = sj.poids_total / temps_restant
    # Facteur exponentiel pour prioriser les jobs qui arrivent en limite de fenêtre
    if ratio >= 0.9: ratio *= 10 
    return ratio

def get_couloir(sj):
    """Identifiant unique du couloir (A->B ou B->A)"""
    pts = sorted([sj.points_depart[0], sj.points_arrivee[-1]])
    return f"{pts[0]}<->{pts[1]}"

# =================================================================
# 2. CLASSE POSTE CHAUFFEUR
# =================================================================

class PosteChauffeur:
    def __init__(self, id_p, v_type, site_depot, params_rh):
        self.id_poste = id_p
        self.vehicule_type = v_type
        self.stationnement_initial = site_depot
        self.position_actuelle = site_depot
        
        # États : 'INACTIF', 'PRISE_POSTE', 'DISPONIBLE', 'EN_TRAJET_VIDE', 'EN_MISSION', 'EN_PAUSE', 'FIN_POSTE'
        self.etat = 'INACTIF'
        self.temps_restant_etat = 0
        self.job_en_cours = None
        self.couloir_actuel = None
        
        self.heure_debut_service = None
        self.temps_service_cumule = 0
        self.pause_faite = False
        
        self.historique = []
        self.amplitude_max = params_rh.get('v_duree', 450)
        self.duree_pause = params_rh.get('v_pause', 45)
        self.prise_poste_t = 15 # Fixe selon tes précisions

    def enregistrer(self, minute, activite, sj=None, details=""):
        self.historique.append({
            "Minute_Debut": minute,
            "Heure_Debut": f"{int(minute//60):02d}:{int(minute%60):02d}",
            "Activite": activite,
            "Position": self.position_actuelle,
            "SJ_ID": sj.liste_jobs[0].flux_id if sj else "N/A",
            "Details": details
        })

# =================================================================
# 3. LOGIQUE D'ORDONNANCEMENT
# =================================================================

def simuler_journee(I, liste_sj, v_type_cible, matrice_duree, params_logistique):
    """
    Simule une journée avec I camions pour un type donné.
    """
    rh = params_logistique.get('rh', {})
    h_start = to_min(rh.get('h_prise_min', 360))
    h_end = to_min(rh.get('h_fin_max', 1260))
    pas = 5
    
    # Initialisation des postes
    postes = []
    # On récupère le dépôt depuis les params véhicules (ici simplifié)
    depot = "DEPOT_SITE" # À mapper avec ton df_vehicules
    for i in range(I):
        postes.append(PosteChauffeur(f"{v_type_cible}_{i+1}", v_type_cible, depot, rh))

    jobs_restants = [sj for sj in liste_sj if sj.v_type == v_type_cible]
    minute = h_start

    while minute <= h_end:
        # 1. Mise à jour des postes
        for p in postes:
            if p.temps_restant_etat > 0:
                p.temps_restant_etat -= pas
                continue
            
            # Si une tâche vient de se finir
            if p.etat == 'PRISE_POSTE':
                p.etat = 'DISPONIBLE'
            elif p.etat == 'EN_TRAJET_VIDE':
                p.position_actuelle = p.job_en_cours.points_depart[0]
                p.etat = 'EN_MISSION'
                p.temps_restant_etat = p.job_en_cours.poids_total
                p.enregistrer(minute, "EN_MISSION", p.job_en_cours)
            elif p.etat == 'EN_MISSION':
                # VERIFICATION DEADLINE
                if minute > p.job_en_cours.h_deadline_min:
                    return None # Échec : deadline ratée
                p.position_actuelle = p.job_en_cours.points_arrivee[-1]
                p.couloir_actuel = get_couloir(p.job_en_cours)
                p.job_en_cours = None
                p.etat = 'DISPONIBLE'
            elif p.etat == 'EN_PAUSE':
                p.etat = 'DISPONIBLE'
            elif p.etat == 'RETOUR_DEPOT':
                p.position_actuelle = p.stationnement_initial
                if p.temps_service_cumule >= p.amplitude_max:
                    p.etat = 'FIN_POSTE'
                else:
                    p.etat = 'EN_PAUSE'
                    p.temps_restant_etat = p.duree_pause
                    p.pause_faite = True
                    p.enregistrer(minute, "EN_PAUSE")

        # 2. Jobs disponibles et calcul Stress
        dispos = [j for j in jobs_restants if j.h_dispo_min <= minute]
        for j in dispos:
            j.current_stress = calculer_stress(j, minute)
        
        dispos.sort(key=lambda x: x.current_stress, reverse=True)
        top_n = dispos[:5] # On regarde les 5 plus stressés

        # 3. Affectation
        for p in postes:
            if p.etat == 'INACTIF':
                # Un nouveau chauffeur prend son service si besoin
                if dispos:
                    p.etat = 'PRISE_POSTE'
                    p.temps_restant_etat = p.prise_poste_t
                    p.heure_debut_service = minute
                    p.enregistrer(minute, "PRISE_POSTE")
            
            elif p.etat == 'DISPONIBLE':
                # Calcul temps service
                p.temps_service_cumule = minute - p.heure_debut_service
                
                # A. Besoin de pause ou fin de journée ?
                if (p.temps_service_cumule >= p.amplitude_max / 2 and not p.pause_faite) or \
                   (p.temps_service_cumule >= p.amplitude_max - 60):
                    
                    if p.position_actuelle == p.stationnement_initial:
                        p.etat = 'EN_PAUSE' if not p.pause_faite else 'FIN_POSTE'
                        p.temps_restant_etat = p.duree_pause if p.etat == 'EN_PAUSE' else 999
                        p.pause_faite = True
                        p.enregistrer(minute, p.etat)
                    else:
                        p.etat = 'RETOUR_DEPOT'
                        p.temps_restant_etat = matrice_duree.get(p.position_actuelle, {}).get(p.stationnement_initial, 30)
                        p.enregistrer(minute, "RETOUR_DEPOT", details="Retour pour pause/fin")
                    continue

                # B. Recherche du meilleur Job
                if not top_n: continue
                
                best_j = None
                # Priorité 1 : Même couloir et origine = position actuelle
                for j in top_n:
                    if get_couloir(j) == p.couloir_actuel and j.points_depart[0] == p.position_actuelle:
                        best_j = j; break
                
                # Priorité 2 : Origine = position actuelle (évite trajet vide)
                if not best_j:
                    for j in top_n:
                        if j.points_depart[0] == p.position_actuelle:
                            best_j = j; break
                
                # Priorité 3 : Même groupe d'origine (logique Hub)
                if not best_j:
                    for j in top_n:
                        if getattr(j, 'origine_group', '') == getattr(p, 'position_group', ''):
                            best_j = j; break
                
                # Priorité 4 : Proche voisin + Stress
                if not best_j:
                    # On re-score les top_n par (distance / stress)
                    scored_candidates = []
                    for j in top_n:
                        dist = matrice_duree.get(p.position_actuelle, {}).get(j.points_depart[0], 99)
                        # Plus le score est bas, meilleur c'est
                        rank_score = dist - (j.current_stress * 10) 
                        scored_candidates.append((rank_score, j))
                    scored_candidates.sort(key=lambda x: x[0])
                    best_j = scored_candidates[0][1]

                if best_j:
                    p.job_en_cours = best_j
                    jobs_restants.remove(best_j)
                    top_n.remove(best_j)
                    dispos.remove(best_j)
                    
                    dist_approche = matrice_duree.get(p.position_actuelle, {}).get(best_j.points_depart[0], 0)
                    if dist_approche > 0:
                        p.etat = 'EN_TRAJET_VIDE'
                        p.temps_restant_etat = dist_approche
                        p.enregistrer(minute, "EN_TRAJET_VIDE", best_j)
                    else:
                        p.etat = 'EN_MISSION'
                        p.temps_restant_etat = best_j.poids_total
                        p.enregistrer(minute, "EN_MISSION", best_j)

        minute += pas

    return postes if not jobs_restants else None

# =================================================================
# 4. FONCTION PRINCIPALE (BOUCLE I)
# =================================================================

def trouver_meilleure_configuration_journee(liste_sj, n_max_dict, df_vehicules, matrice_duree, params_logistique):
    """
    Pour chaque type de véhicule, cherche le I minimum.
    """
    resultats_finaux = {}
    postes_complets = []

    for v_type, n_max in n_max_dict.items():
        st.write(f"🔍 Optimisation pour {v_type} (Max possible : {n_max})...")
        succes_v_type = False
        
        for I in range(1, int(n_max) + 1):
            res_sim = simuler_journee(I, liste_sj, v_type, matrice_duree, params_logistique)
            
            if res_sim is not None:
                st.success(f"✅ Solution trouvée avec {I} véhicules pour {v_type}")
                resultats_finaux[v_type] = I
                postes_complets.extend(res_sim)
                succes_v_type = True
                break
        
        if not succes_v_type:
            st.error(f"❌ Impossible de caser tous les jobs {v_type} même avec {n_max} camions.")

    return {
        "succes": len(resultats_finaux) == len(n_max_dict),
        "postes": postes_complets,
        "nb_vehicules": resultats_finaux
    }

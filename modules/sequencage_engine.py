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

def calculer_stress_dynamique(sj, minute_actuelle):
    """
    Calcule l'urgence d'un SuperJob.
    Stress = Poids_Total / (Deadline - Minute_Actuelle)
    """
    temps_restant = sj.h_deadline_min - minute_actuelle
    if temps_restant <= 0:
        return 9999.0  # Retard ou urgence absolue
    
    ratio = sj.poids_total / temps_restant
    # Facteur d'accélération du stress quand le ratio approche 1
    return ratio * (1 / max(0.01, (1.1 - ratio)))

def get_couloir_id(sj):
    """Identifie l'axe du job (A<->B) pour la priorité de couloir."""
    pts = sorted([sj.points_depart[0], sj.points_arrivee[-1]])
    return f"{pts[0]}--{pts[1]}"

# =================================================================
# 2. CLASSE POSTE CHAUFFEUR
# =================================================================

class PosteChauffeur:
    def __init__(self, id_p, v_type, site_depot, params_rh):
        self.id_poste = id_p
        self.vehicule_type = v_type
        self.stationnement_initial = site_depot
        self.position_actuelle = site_depot
        
        self.etat = 'INACTIF' # INACTIF, PRISE_POSTE, DISPONIBLE, EN_TRAJET_VIDE, EN_MISSION, EN_PAUSE
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
            "Heure_Debut": f"{int(minute//60):02d}:{int(minute%60):02d}",
            "Activite": activite,
            "SJ_ID": sj.liste_jobs[0].flux_id if sj and sj.liste_jobs else "N/A",
            "Details": details
        })

# =================================================================
# 3. MOTEUR DE SIMULATION (PAS DE 5 MIN)
# =================================================================

def simuler_faisabilite(I, liste_sj_type, v_type, matrice_duree, params_logistique, df_vehicules):
    """
    Tente de réaliser tous les jobs avec I camions.
    Retourne la liste des postes si succès, None si échec (retard).
    """
    rh = params_logistique.get('rh', {})
    h_start = to_min(rh.get('h_prise_min', 360))
    h_end = to_min(rh.get('h_fin_max', 1260))
    pas = 5
    
    # Identification du dépôt pour ce type de véhicule
    # On cherche dans df_vehicules la ligne correspondant au type
    depot_initial = "DEPOT"
    try:
        row_v = df_vehicules[df_vehicules['Types'] == v_type].iloc[0]
        depot_initial = row_v.get('Stationnement initial', "DEPOT")
    except:
        pass
    
    postes = [PosteChauffeur(f"{v_type}_{i+1}", v_type, depot_initial, rh) for i in range(I)]
    jobs_restants = list(liste_sj_type)
    minute = h_start

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
                # VERIFICATION RETARD
                if minute > p.job_en_cours.h_deadline_min:
                    return None # Échec : Deadline dépassée
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

        # B. Affectation des nouveaux jobs
        dispos = [j for j in jobs_restants if j.h_dispo_min <= minute]
        for j in dispos:
            j.stress = calculer_stress_dynamique(j, minute)
        dispos.sort(key=lambda x: x.stress, reverse=True)

        for p in postes:
            if p.etat == 'INACTIF' and dispos:
                p.etat = 'PRISE_POSTE'
                p.temps_restant_etat = 15 # Prise de poste fixe
                p.h_debut_service = minute
                p.enregistrer(minute, "PRISE_POSTE")
            
            elif p.etat == 'DISPONIBLE':
                # 1. Vérification besoin de pause (mi-parcours)
                temps_travail = minute - p.h_debut_service
                if temps_travail >= (p.amplitude_max / 2) and not p.pause_faite:
                    if p.position_actuelle == p.stationnement_initial:
                        p.etat = 'EN_PAUSE'
                        p.temps_restant_etat = p.duree_pause
                        p.pause_faite = True
                        p.enregistrer(minute, "EN_PAUSE")
                    else:
                        dist = matrice_duree.get(p.position_actuelle, {}).get(p.stationnement_initial, 30)
                        p.etat = 'RETOUR_DEPOT'
                        p.temps_restant_etat = dist
                        p.enregistrer(minute, "EN_TRAJET_VIDE", details="Retour Dépôt pour Pause")
                    continue

                if not dispos:
                    continue

                # 2. Sélection du meilleur job parmi les N plus stressés
                top_candidates = dispos[:5]
                best_sj = None
                
                # Priorité : Même couloir ET sur place
                for j in top_candidates:
                    if get_couloir_id(j) == p.couloir_actuel and j.points_depart[0] == p.position_actuelle:
                        best_sj = j; break
                
                # Priorité : Sur place (n'importe quel couloir)
                if not best_sj:
                    for j in top_candidates:
                        if j.points_depart[0] == p.position_actuelle:
                            best_sj = j; break
                
                # Priorité : Proche voisin combiné au stress
                if not best_sj:
                    top_candidates.sort(key=lambda x: matrice_duree.get(p.position_actuelle, {}).get(x.points_depart[0], 999) - x.stress)
                    best_sj = top_candidates[0]

                if best_sj:
                    p.job_en_cours = best_sj
                    jobs_restants.remove(best_sj)
                    dispos.remove(best_sj)
                    
                    dist_approche = matrice_duree.get(p.position_actuelle, {}).get(best_sj.points_depart[0], 0)
                    if dist_approche > 0:
                        p.etat = 'EN_TRAJET_VIDE'
                        p.temps_restant_etat = dist_approche
                        p.enregistrer(minute, "EN_TRAJET_VIDE", best_sj)
                    else:
                        p.etat = 'EN_MISSION'
                        p.temps_restant_etat = best_sj.poids_total
                        p.enregistrer(minute, "EN_MISSION", best_sj)

        if not jobs_restants:
            return postes
        
        minute += pas

    return None

# =================================================================
# 4. FONCTION PRINCIPALE (BOUCLE I)
# =================================================================

def trouver_meilleure_configuration_journee(liste_sj, n_max_dict, df_vehicules, matrice_duree, params_logistique):
    """
    Fonction appelée par app.py. 
    Parcourt I de 1 à Nmax pour trouver la solution optimale.
    """
    postes_complets = []
    
    for v_type, val_max in n_max_dict.items():
        # Gestion du format de n_max (liste d'intensités vs nombre)
        if isinstance(val_max, list):
            n_max_calc = math.ceil(max(val_max) * 2)
        else:
            n_max_calc = math.ceil(max(val_max) * 2)+1
            
        st.info(f"Analyse du type **{v_type}** (Recherche jusqu'à {n_max_calc} véhicules)")
        
        jobs_v = [sj for sj in liste_sj if sj.v_type == v_type]
        if not jobs_v:
            continue
            
        solution_trouvee = False
        # BOUCLE I : On cherche le nombre minimal de camions
        for I in range(1, n_max_calc + 1):
            resultat = simuler_faisabilite(I, jobs_v, v_type, matrice_duree, params_logistique, df_vehicules)
            
            if resultat is not None:
                st.success(f"✅ {v_type} : Solution trouvée avec **{I}** véhicule(s).")
                postes_complets.extend(resultat)
                solution_trouvee = True
                break
        
        if not solution_trouvee:
            st.error(f"❌ {v_type} : Aucun planning valide trouvé avec {n_max_calc} véhicules.")

    return {"succes": len(postes_complets) > 0, "postes": postes_complets}

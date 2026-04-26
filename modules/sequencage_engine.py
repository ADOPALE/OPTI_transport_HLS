import pandas as pd
import math
import streamlit as st
from datetime import time, datetime, timedelta

# =================================================================
# 1. UTILITAIRES & LOGIQUE DE SÉLECTION (MISES À JOUR)
# =================================================================

def to_min(t):
    if isinstance(t, (time, datetime)):
        return t.hour * 60 + t.minute
    return float(t)

def get_couloir_id(sj):
    # Utilise le premier point de départ et le dernier point d'arrivée du bloc
    pts = sorted([sj.points_depart[0], sj.points_arrivee[-1]])
    return f"{pts[0]}--{pts[1]}"

def est_sj_disponible_dynamique(sj, minute_actuelle, matrice_duree):
    """
    Vérifie si le SJ est 'exécutable' sans attente excessive.
    On vérifie que pour CHAQUE segment, le camion n'arrive pas avant h_dispo.
    """
    # 1. Le camion doit au moins pouvoir démarrer le premier job
    if to_min(sj.h_dispo_min) > minute_actuelle:
        return False
    
    temps_cumule = minute_actuelle
    # On simule le trajet interne du SuperJob
    # Note : Le SuperJob contient déjà la logique de trajet dans sj.poids_total,
    # mais ici on vérifie la synchronisation avec les h_dispo de chaque maillon.
    
    pos_actuelle = sj.points_depart[0]
    
    for i, job in enumerate(sj.liste_jobs):
        # Trajet pour aller au point de collecte du job i
        trajet_vers_collecte = matrice_duree.get(pos_actuelle, {}).get(job.origin, 0)
        temps_cumule += trajet_vers_collecte
        
        # Si le camion arrive avant que le job ne soit prêt :
        # On considère que le SJ n'est pas encore 'disponible' pour garantir la fluidité
        if temps_cumule < to_min(job.h_dispo):
            return False
        
        # Le temps avance : Manutention + Trajet vers livraison
        # On utilise ici une estimation simplifiée ou le poids du job
        temps_cumule += (job.poids_total if hasattr(job, 'poids_total') else 30)
        pos_actuelle = job.destination
        
    return True

def calculer_stress_maillon_critique(sj, minute_actuelle, matrice_duree, p_position_actuelle):
    """
    Calcule la tension sur le maillon le plus 'en retard' du SuperJob.
    """
    scores_stress = []
    temps_cumule = minute_actuelle
    
    # 1. Approche initiale du camion vers le début du SJ
    dist_approche = matrice_duree.get(p_position_actuelle, {}).get(sj.points_depart[0], 0)
    temps_cumule += dist_approche
    
    pos_simulee = sj.points_depart[0]
    
    for job in sj.liste_jobs:
        # Trajet vers le point de collecte de ce job
        trajet_interne = matrice_duree.get(pos_simulee, {}).get(job.origin, 0)
        temps_cumule += trajet_interne
        
        # Temps de fin estimé pour ce maillon (Manut + Livraison)
        duree_mission = (job.poids_total if hasattr(job, 'poids_total') else 30)
        heure_fin_estimee = temps_cumule + duree_mission
        
        deadline_job = to_min(job.h_deadline)
        marge = deadline_job - heure_fin_estimee
        
        # Score de stress (plus la marge est faible, plus le score est élevé)
        scores_stress.append(1000 - marge)
        
        # On met à jour pour le maillon suivant
        temps_cumule = heure_fin_estimee
        pos_simulee = job.destination

    return max(scores_stress)

# =================================================================
# 2. CLASSE POSTE CHAUFFEUR (Inchangée mais nécessaire)
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
        self.h_debut_service_actuel = None 
        self.pause_faite = False
        self.historique = []
        self.amplitude_max = params_rh.get('amplitude_totale', 450)
        self.duree_pause = params_rh.get('pause', 45)
        self.temps_passation = params_rh.get('temps_fixes', 30)

    def enregistrer(self, minute, activite, sj=None, details=""):
        sj_id = sj.super_job_id if sj else "N/A"
        self.historique.append({
            "Minute_Debut": minute,
            "Heure_Debut": f"{int(minute//60):02d}h{int(minute%60):02d}",
            "Activite": activite,
            "SJ_ID": sj_id,
            "Details": details
        })

# =================================================================
# 3. MOTEUR DE SIMULATION (Ajusté pour SuperJob)
# =================================================================

def selectionner_meilleur_job(p, dispos, minute, matrice_duree):
    candidats_evalues = []
    for j in dispos:
        if est_sj_disponible_dynamique(j, minute, matrice_duree):
            stress_max = calculer_stress_maillon_critique(j, minute, matrice_duree, p.position_actuelle)
            dist_approche = matrice_duree.get(p.position_actuelle, {}).get(j.points_depart[0], 0)
            bonus_couloir = 50 if get_couloir_id(j) == p.couloir_actuel else 0
            
            # Arbitrage Pareto : Stress (Priorité) vs Proximité
            score = stress_max + bonus_couloir - (dist_approche * 1.5)
            candidats_evalues.append({'job': j, 'score': score})

    if not candidats_evalues: return None
    candidats_evalues.sort(key=lambda x: x['score'], reverse=True)
    return candidats_evalues[0]['job']

def simuler_faisabilite(I, liste_sj_type, v_type, matrice_duree, params_logistique, df_vehicules):
    rh = params_logistique.get('rh', {})
    h_start = to_min(rh.get('h_prise_min', 360))
    h_end = to_min(rh.get('h_fin_max', 1380)) # Extension possible jusqu'à 23h
    pas = 5
    
    try:
        depot_initial = df_vehicules[df_vehicules['Types'] == v_type]['Stationnement initial'].iloc[0]
    except:
        depot_initial = "DEPOT"

    postes = [PosteChauffeur(f"{v_type}_{i+1}", v_type, depot_initial, rh) for i in range(I)]
    jobs_restants = list(liste_sj_type)
    minute = h_start

    while minute <= h_end:
        # 1. Mise à jour des temps restants pour chaque véhicule
        for p in postes:
            if p.temps_restant_etat > 0:
                p.temps_restant_etat -= pas
                continue
            
            # --- Transitions d'états ---
            if p.etat == 'PRISE_POSTE':
                p.etat = 'DISPONIBLE'
            elif p.etat == 'EN_TRAJET_VIDE':
                if p.job_en_cours:
                    p.position_actuelle = p.job_en_cours.points_depart[0]
                    p.etat = 'EN_MISSION'
                    p.temps_restant_etat = p.job_en_cours.poids_total
                    p.enregistrer(minute, "EN_MISSION", p.job_en_cours)
                else:
                    p.position_actuelle = p.stationnement_initial
                    p.etat = 'TRANSITION'
            elif p.etat == 'EN_MISSION':
                p.position_actuelle = p.job_en_cours.points_arrivee[-1]
                p.couloir_actuel = get_couloir_id(p.job_en_cours)
                p.etat = 'DISPONIBLE'
                p.job_en_cours = None
            elif p.etat == 'TRANSITION':
                temps_travaille = minute - p.h_debut_service_actuel
                if temps_travaille >= p.amplitude_max:
                    p.etat = 'PASSATION_POSTE'; p.temps_restant_etat = p.temps_passation
                    p.enregistrer(minute, "PASSATION_POSTE")
                else:
                    p.etat = 'EN_PAUSE'; p.temps_restant_etat = p.duree_pause
                    p.pause_faite = True
                    p.enregistrer(minute, "EN_PAUSE")
            elif p.etat in ['EN_PAUSE', 'PASSATION_POSTE']:
                if p.etat == 'PASSATION_POSTE': p.h_debut_service_actuel = minute; p.pause_faite = False
                p.etat = 'DISPONIBLE'

        # 2. Affectation des SuperJobs
        dispos = [j for j in jobs_restants if to_min(j.h_dispo_min) <= minute]
        
        for p in postes:
            if p.temps_restant_etat > 0: continue
            
            if p.etat == 'INACTIF' and dispos:
                p.etat = 'PRISE_POSTE'; p.temps_restant_etat = 15
                p.h_debut_service_actuel = minute
                p.enregistrer(minute, "PRISE_POSTE")
            
            elif p.etat == 'DISPONIBLE':
                temps_travaille = minute - p.h_debut_service_actuel
                dist_retour = matrice_duree.get(p.position_actuelle, {}).get(p.stationnement_initial, 30)

                # Sécurité Amplitude (Retour Dépôt)
                if (temps_travaille >= p.amplitude_max / 2 and not p.pause_faite) or (temps_travaille >= p.amplitude_max - 45):
                    p.etat = 'EN_TRAJET_VIDE'; p.temps_restant_etat = dist_retour
                    p.enregistrer(minute, "EN_TRAJET_VIDE", details="Retour Dépôt (Pause/Relève)")
                    continue

                if not dispos: continue
                
                best_sj = selectionner_meilleur_job(p, dispos, minute, matrice_duree)
                if best_sj:
                    dist_approche = matrice_duree.get(p.position_actuelle, {}).get(best_sj.points_depart[0], 0)
                    # Validation amplitude : Trajet + Mission + Retour
                    if (minute + dist_approche + best_sj.poids_total + dist_retour) <= (p.h_debut_service_actuel + p.amplitude_max + 15):
                        p.job_en_cours = best_sj
                        jobs_restants.remove(best_sj)
                        dispos.remove(best_sj)
                        p.etat = 'EN_TRAJET_VIDE'; p.temps_restant_etat = dist_approche
                        p.enregistrer(minute, "EN_TRAJET_VIDE", best_sj, "Approche Mission")

        if not jobs_restants: return postes
        minute += pas

    return None

# =================================================================
# 4. FONCTION D'ENTRÉE PRINCIPALE
# =================================================================

def trouver_meilleure_configuration_journee(liste_sj, n_max_dict, df_vehicules, matrice_duree, params_logistique):
    postes_complets = []
    for v_type, val_max in n_max_dict.items():
        # On utilise le pic d'intensité calculé précédemment comme point de départ
        pic_charge = max(val_max) if isinstance(val_max, list) else val_max
        n_depart = max(1, math.floor(pic_charge * 0.8)) # On tente d'abord avec un peu moins que le pic
        n_limite = math.ceil(pic_charge * 1.5) # Limite haute de recherche
        
        st.info(f"Analyse **{v_type}** : Tentative d'optimisation des ressources...")
        
        jobs_v = [sj for sj in liste_sj if sj.v_type == v_type]
        if not jobs_v: continue
            
        solution_trouvee = False
        for I in range(n_depart, n_limite + 1):
            res = simuler_faisabilite(I, jobs_v, v_type, matrice_duree, params_logistique, df_vehicules)
            if res:
                st.success(f"✅ **{v_type}** : **{I}** véhicule(s) suffisent pour couvrir l'activité.")
                postes_complets.extend(res)
                solution_trouvee = True
                break
        
        if not solution_trouvee:
            st.error(f"❌ **{v_type}** : Impossible de trouver un planning. Vérifiez les deadlines.")

    return {"succes": len(postes_complets) > 0, "postes": postes_complets}

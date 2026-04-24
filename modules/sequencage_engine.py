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
        
        # --- AJOUTS CRITIQUES ---
        self.vehicule_id = None  # Sera lié à l'ID du camion physique
        self.amplitude_max = params_rh.get('amplitude_totale', 540)
        self.duree_pause = params_rh.get('pause', 45)
        
        # On récupère t_fin depuis temps_fixes (souvent t_prise + t_fin)
        # Si temps_fixes = 30, on alloue 15min à la fin
        t_fixes = params_rh.get('temps_fixes', 30)
        self.t_fin = t_fixes / 2 

    def enregistrer(self, minute, activite, sj=None, details=""):
        self.historique.append({
            "Minute_Debut": minute,
            "Heure_Debut": f"{int(minute//60):02d}h{int(minute%60):02d}",
            "Activite": activite,
            "SJ_ID": sj.liste_jobs[0].flux_id if sj and sj.liste_jobs else "N/A",
            "Details": details,
            "Vehicule_Physique": self.vehicule_id
        })


# =================================================================
# 3. LOGIQUE DE SÉLECTION (MAILLON CRITIQUE)
# =================================================================

def est_sj_disponible_dynamique(sj, minute_actuelle, matrice_duree):
    """
    Vérifie si, en partant maintenant, chaque job du SJ sera prêt 
    au moment où le camion arrivera à son point de collecte.
    """
    # 1. Condition de base : le premier job doit être prêt
    if to_min(sj.liste_jobs[0].h_dispo) > minute_actuelle:
        return False
    
    temps_cumule = minute_actuelle
    position_simulee = sj.points_depart[0] # On est déjà au départ du 1er
    
    # 2. On parcourt les jobs du SJ pour vérifier les dispos suivantes
    for i, job in enumerate(sj.liste_jobs):
        # Si ce n'est pas le premier, on ajoute le trajet depuis la fin du job précédent
        if i > 0:
            trajet_interne = matrice_duree.get(position_simulee, {}).get(job.origin, 0)
            temps_cumule += trajet_interne
            
            # Vérification : le job i est-il prêt quand on arrive ?
            if temps_cumule < to_min(job.h_dispo):
                return False # Le camion arriverait trop tôt, le SJ n'est pas "fluide"
        
        # On ajoute la durée du job i (manutention + trajet livraison)
        temps_cumule += job.poids_total if hasattr(job, 'poids_total') else 30 # fallback
        position_simulee = job.destination
        
    return True


def calculer_stress_maillon_critique(sj, minute_actuelle, matrice_duree, p_position_actuelle):
    """
    Calcule le stress de chaque job i du SJ et renvoie le maximum.
    """
    scores_stress = []
    temps_cumule = minute_actuelle
    
    # Ajout du trajet d'approche initial du camion
    dist_approche = matrice_duree.get(p_position_actuelle, {}).get(sj.points_depart[0], 0)
    temps_cumule += dist_approche
    
    position_simulee = sj.points_depart[0]
    
    for job in sj.liste_jobs:
        # Temps pour finir CE job spécifique
        # (Si multi-jobs, on ajoute le trajet depuis le drop précédent vers ce pick-up)
        trajet_interne = matrice_duree.get(position_simulee, {}).get(job.origin, 0)
        temps_cumule += trajet_interne
        
        heure_fin_estimee = temps_cumule + (job.poids_total if hasattr(job, 'poids_total') else 20)
        deadline_job = to_min(job.h_deadline)
        
        # Stress relatif : (Heure Max - Heure Fin estimée)
        # Plus la marge est petite (ou négative), plus le stress est grand
        marge = deadline_job - heure_fin_estimee
        
        # Transformation en score (plus c'est petit/négatif, plus le score est haut)
        # On peut utiliser : 1000 - marge
        scores_stress.append(1000 - marge)
        
        # Mise à jour pour le maillon suivant
        temps_cumule = heure_fin_estimee
        position_simulee = job.destination

    return max(scores_stress) # Le maillon le plus stressé dicte la priorité du SJ



def selectionner_meilleur_job(p, dispos, minute, matrice_duree, h_fin_max_theorique):
    """
    Version simplifiée : Priorité au stress et à l'efficacité.
    Le retour au dépôt est géré uniquement par la boucle principale.
    """
    candidats_evalues = []
    
    # On définit juste la fin de vie du chauffeur (Amplitude pure)
    h_fin_service = p.h_debut_service + p.amplitude_max
    
    for j in dispos:
        # 1. Disponibilité dynamique (Check si les flux sont prêts)
        if not est_sj_disponible_dynamique(j, minute, matrice_duree):
            continue
            
        # 2. Faisabilité simple : Est-ce qu'on finit le job avant la fin de l'amplitude ?
        dist_approche = matrice_duree.get(p.position_actuelle, {}).get(j.points_depart[0], 0)
        duree_sj = j.poids_total
        
        # On ne teste QUE l'approche + le job. Le retour se fera après.
        if minute + dist_approche + duree_sj <= h_fin_service:
            
            # CALCUL DU SCORE (Identique à ta logique cible)
            stress = calculer_stress_maillon_critique(j, minute, matrice_duree, p.position_actuelle)
            
            # Arbitrage Urgence vs Proximité
            # On pénalise la distance d'approche pour éviter les trajets à vide inutiles
            score = stress - (dist_approche * 1.5)
            
            # Bonus de positionnement
            if j.points_depart[0] == p.position_actuelle: score += 50
            if get_couloir_id(j) == p.couloir_actuel: score += 30
            
            candidats_evalues.append({'job': j, 'score': score})

    if not candidats_evalues:
        return None

    # On prend le meilleur score
    candidats_evalues.sort(key=lambda x: x['score'], reverse=True)
    return candidats_evalues[0]['job']


# =================================================================
# 4. MOTEUR DE SIMULATION
# =================================================================


def simuler_faisabilite(I, liste_sj_type, v_type, matrice_duree, params_logistique, df_vehicules):
    rh = params_logistique.get('rh', {})
    h_start = to_min(rh.get('h_prise_min', 360))
    h_fin_max_theorique = to_min(rh.get('h_fin_max', 1260))
    t_prise = rh.get('temps_fixes', 30) / 2
    pas = 5
    
    try:
        depot = df_vehicules[df_vehicules['Types'] == v_type]['Stationnement initial'].iloc[0]
    except: depot = "DEPOT_SITE"

    # I représente le nombre de camions physiques
    vehicules_physiques = [{"id": i, "dispo_a": h_start, "pos": depot} for i in range(I)]
    
    postes_actifs = []
    postes_termines = []
    jobs_restants = list(liste_sj_type)
    minute = h_start

    while minute <= h_fin_max_theorique:
        # 1. Gestion des chauffeurs en cours
        for p in postes_actifs[:]:
            if p.temps_restant_etat > 0:
                p.temps_restant_etat -= pas
                continue
            
            if p.etat == 'PRISE_POSTE':
                p.etat = 'DISPONIBLE'
            elif p.etat == 'EN_TRAJET_VIDE':
                p.position_actuelle = p.job_en_cours.points_depart[0] if p.job_en_cours else p.stationnement_initial
                if p.job_en_cours:
                    p.etat = 'EN_MISSION'; p.temps_restant_etat = p.job_en_cours.poids_total
                    p.enregistrer(minute, "EN_MISSION", p.job_en_cours)
                else: 
                    p.etat = 'RETOUR_DEPOT'
            elif p.etat == 'EN_MISSION':
                p.position_actuelle = p.job_en_cours.points_arrivee[-1]
                p.couloir_actuel = get_couloir_id(p.job_en_cours)
                p.etat = 'DISPONIBLE'; p.job_en_cours = None
            elif p.etat == 'RETOUR_DEPOT':
                # LIBÉRATION DU CAMION
                for v in vehicules_physiques:
                    if v['id'] == p.vehicule_id:
                        v['dispo_a'] = minute + p.t_fin
                        v['pos'] = p.stationnement_initial
                
                p.enregistrer(minute, "FIN_DE_POSTE")
                postes_termines.append(p)
                postes_actifs.remove(p)

        # 2. Création de postes (Chauffeurs) sur les camions libres
        dispos = [j for j in jobs_restants if to_min(j.liste_jobs[0].h_dispo) <= minute]
        
        camions_libres = [v for v in vehicules_physiques 
                          if v['dispo_a'] <= minute 
                          and not any(p.vehicule_id == v['id'] for p in postes_actifs)]

        for v in camions_libres:
            if dispos:
                # On crée un chauffeur pour ce camion
                nouveau_p = PosteChauffeur(f"CH_{v['id']}_{minute}", v_type, v['pos'], rh)
                nouveau_p.vehicule_id = v['id']
                nouveau_p.h_debut_service = minute
                nouveau_p.etat = 'PRISE_POSTE'; nouveau_p.temps_restant_etat = t_prise
                nouveau_p.enregistrer(minute, "PRISE_POSTE", details=f"Prise du camion {v['id']}")
                postes_actifs.append(nouveau_p)
                camions_libres.remove(v)

        # 3. Affectation des jobs
        for p in [p for p in postes_actifs if p.etat == 'DISPONIBLE']:
            # Check Fin d'amplitude (Retour Dépôt)
            dist_r = matrice_duree.get(p.position_actuelle, {}).get(p.stationnement_initial, 30)
            # Utilisation correcte de p.t_fin
            if minute + dist_r + p.t_fin >= (p.h_debut_service + p.amplitude_max):
                p.etat = 'RETOUR_DEPOT'; p.temps_restant_etat = dist_r
                p.enregistrer(minute, "RETOUR_DEPOT", details="Fin service")
                continue

            if not dispos: continue
            
            best_sj = selectionner_meilleur_job(p, dispos, minute, matrice_duree, h_fin_max_theorique)
            if best_sj:
                dist = matrice_duree.get(p.position_actuelle, {}).get(best_sj.points_depart[0], 0)
                p.job_en_cours = best_sj
                jobs_restants.remove(best_sj)
                if best_sj in dispos: dispos.remove(best_sj)
                p.etat = 'EN_TRAJET_VIDE'; p.temps_restant_etat = dist
                p.enregistrer(minute, "EN_TRAJET_VIDE", best_sj)

        if not jobs_restants and not postes_actifs:
            return postes_termines
        
        minute += pas

    return None


# =================================================================
# 5. FONCTION D'ENTREE
# =================================================================

def trouver_meilleure_configuration_journee(liste_sj, n_max_dict, df_vehicules, matrice_duree, params_logistique):
    """
    Boucle sur le nombre de véhicules physiques (I) pour trouver le minimum viable.
    """
    tous_les_postes_finaux = []
    resultats_synthese = {}

    for v_type, val_max in n_max_dict.items():
        # On garde une marge de sécurité sur le max pour la recherche
        n_max_recherche = math.ceil(max(val_max) * 2) if isinstance(val_max, list) else math.ceil(val_max * 2)
        
        st.info(f"🔎 Analyse **{v_type}** : Recherche du nombre de camions physiques (Max testé : {n_max_recherche})")
        
        jobs_v = [sj for sj in liste_sj if sj.v_type == v_type]
        if not jobs_v:
            st.warning(f"⚠️ Aucun job trouvé pour le type {v_type}")
            continue
            
        solution_pour_ce_type = None
        
        # On cherche le nombre minimal de véhicules physiques I
        for I in range(1, n_max_recherche + 1):
            # La fonction simuler_faisabilite renvoie la liste des POSTES (chauffeurs)
            res_postes = simuler_faisabilite(I, jobs_v, v_type, matrice_duree, params_logistique, df_vehicules)
            
            if res_postes:
                # Calcul de la productivité pour le log
                n_chauffeurs = len(res_postes)
                ratio = len(jobs_v) / I
                
                st.success(f"✅ **{v_type}** validé avec **{I} camions** physiques.")
                st.caption(f"📊 Utilisation : {n_chauffeurs} postes chauffeurs créés pour {len(jobs_v)} SuperJobs (Moyenne : {ratio:.1f} SJ/camion)")
                
                solution_pour_ce_type = res_postes
                resultats_synthese[v_type] = I
                break # On a trouvé le minimum de camions, on arrête pour ce type
        
        if solution_pour_ce_type:
            tous_les_postes_finaux.extend(solution_pour_ce_type)
        else:
            st.error(f"❌ **{v_type}** : Impossible de trouver une solution, même avec {n_max_recherche} camions.")

    return {
        "succes": len(tous_les_postes_finaux) > 0,
        "postes": tous_les_postes_finaux,
        "bilan_vehicules": resultats_synthese
    }

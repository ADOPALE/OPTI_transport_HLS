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



def calculer_stress_maillon_critique(sj, minute_actuelle, matrice_duree, p_position_actuelle):
    """
    Calcule le stress basé sur la 'marge de départ' (LST).
    Stress = (Départ au plus tard possible) - (Heure actuelle + Approche).
    Un score élevé (proche de 1000 ou plus) signifie qu'on est proche de l'impossibilité.
    """
    
    # 1. TEMPS D'APPROCHE (Indispensable pour savoir quand on peut réellement commencer)
    dist_approche = matrice_duree.get(p_position_actuelle, {}).get(sj.points_depart[0], 0)
    
    # 2. CALCUL DU DÉPART AU PLUS TARD (LST - Latest Start Time)
    lst_sj = 0

    # --- CAS A : LOGIQUE BLOC (GROUPAGE OU RAMASSAGE) ---
    # Le stress est global : on doit avoir fini le bloc avant la deadline la plus courte.
    if sj.type_logistique in ['GROUPAGE_PUR', 'RAMASSAGE']:
        h_deadline_min = min(to_min(j.h_deadline) for j in sj.liste_jobs)
        # Départ au plus tard = Deadline min - Durée totale de la mission
        lst_sj = h_deadline_min - sj.poids_total

    # --- CAS B : LOGIQUE FILAIRE (DISTRIBUTION OU CHAINAGE) ---
    # On calcule le stress pour chaque job et on prend le plus contraignant (le plus tôt).
    else:
        lst_candidats = []
        temps_cumule_trajets = 0
        pos_precedente = sj.points_depart[0]
        
        for job in sj.liste_jobs:
            # On cumule le trajet depuis le début jusqu'à ce job spécifique
            trajet_interne = matrice_duree.get(pos_precedente, {}).get(job.origin, 0)
            temps_cumule_trajets += trajet_interne
            
            # Pour respecter la deadline de CE job, à quelle heure au plus tard 
            # le camion doit-il avoir quitté le point de départ du SJ ?
            # LST_job = Deadline - (Somme trajets cumulés + durée propre du job)
            duree_propre = (job.poids_total if hasattr(job, 'poids_total') else 30)
            lst_job = to_min(job.h_deadline) - (temps_cumule_trajets + duree_propre)
            
            lst_candidats.append(lst_job)
            
            # On avance la position pour le maillon suivant
            pos_precedente = job.destination
            # On ajoute la durée du job au cumul pour le suivant
            temps_cumule_trajets += duree_propre
            
        # Le départ au plus tard du SJ est dicté par le job le plus "serré"
        lst_sj = min(lst_candidats)

    # 3. CALCUL DU SCORE DE STRESS FINAL
    # Marge réelle = Temps restant avant l'heure fatidique de départ
    # On soustrait l'approche car le camion doit encore voyager vers le SJ.
    marge_depart = lst_sj - (minute_actuelle + dist_approche)
    
    # Score : Plus la marge est petite (ou négative), plus le score est élevé.
    # On utilise 1000 comme base pour rester cohérent avec ton moteur de sélection.
    score_stress = 1000 - marge_depart
    
    return score_stress


def obtenir_couloir_groupage_prioritaire(jobs_restants):
    """
    Identifie le couloir (Zone A -> Zone B) ayant le plus de trajets 
    cumulés dans des SuperJobs de type GROUPAGE_PUR.
    """
    stats_couloirs = {}
    
    for sj in jobs_restants:
        if sj.type_logistique == 'GROUPAGE_PUR':
            # On définit le couloir par les zones (3 premières lettres)
            zone_dep = sj.points_depart[0][:3]
            zone_arr = sj.points_arrivee[-1][:3]
            c_id = f"{zone_dep}--{zone_arr}"
            
            # On compte le nombre de jobs (trajets) contenus dans ce SuperJob
            nb_trajets = len(sj.liste_jobs)
            stats_couloirs[c_id] = stats_couloirs.get(c_id, 0) + nb_trajets
            
    if not stats_couloirs:
        return None
        
    # Retourne l'ID du couloir le plus chargé en trajets de groupage
    return max(stats_couloirs, key=stats_couloirs.get)



# =================================================================
# 2. CLASSE POSTE CHAUFFEUR (Inchangée mais nécessaire)
# =================================================================

class PosteChauffeur:
    def __init__(self, id_p, v_type, site_depot, params_rh):
        self.id_poste = id_p
        self.vehicule_type = v_type
        self.stationnement_initial = site_depot
        self.position_actuelle = site_depot
        self.etat = 'INACTIF'  # INACTIF, PRISE_POSTE, DISPONIBLE, EN_TRAJET_VIDE, EN_MISSION, PAUSE, FIN_DE_SERVICE
        self.temps_restant_etat = 0
        self.job_en_cours = None
        self.couloir_actuel = None
        self.h_debut_service_actuel = None 
        self.pause_faite = False
        self.historique = []
        self.amplitude_max = params_rh.get('amplitude_totale', 540) # ex: 9h
        self.duree_pause = params_rh.get('pause', 45)
        self.temps_passation = params_rh.get('temps_fixes', 30)
        self.vehicule_deja_affecte = False # Pour savoir si on initialise à t ou t-15
        
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

def selectionner_meilleur_job(p, dispos, minute, matrice_duree, I_simule, jobs_restants, est_premier_job=False):
    """
    Sélectionne le meilleur SuperJob pour le chauffeur 'p'.
    Logique : 
    1. Top N jobs les plus stressés.
    2. Priorité : Même couloir/Sur place > Sur place > Même zone.
    3. Sinon : Le plus proche parmi le Top N.
    """
    if not dispos:
        return None

    liste_candidats = []
    # --- RÈGLE STRATÉGIQUE : 1er JOB (Priorité Groupage Massif) ---
    if est_premier_job:
        c_prioritaire = obtenir_couloir_groupage_prioritaire(jobs_restants)
        
        if c_prioritaire:
            # On cherche dans les dispos un SJ qui correspond à ce couloir (en zones)
            candidats = []
            for sj in dispos:
                z_dep = sj.points_depart[0][:3]
                z_arr = sj.points_arrivee[-1][:3]
                if f"{z_dep}--{z_arr}" == c_prioritaire:
                    candidats.append(sj)
            
            if candidats:
                # On prend le plus urgent (stress) parmi ces candidats stratégiques
                candidats.sort(key=lambda x: calculer_stress_maillon_critique(x, minute, matrice_duree, p.position_actuelle), reverse=True)
                return candidats[0]

    # --- 1. CALCUL DU STRESS ET TRI INITIAL ---
    
    for sj in dispos:
        # Calcul de l'urgence (LST)
        stress = calculer_stress_maillon_critique(sj, minute, matrice_duree, p.position_actuelle)
        liste_candidats.append({'sj': sj, 'stress': stress})
    
    # Tri par stress décroissant (le plus urgent en premier)
    liste_candidats.sort(key=lambda x: x['stress'], reverse=True)

    # --- 2. RESTRICTION AU TOP N (N = I_simule) ---
    # On ne travaille que sur les 'I' missions les plus urgentes
    top_n_jobs = [item['sj'] for item in liste_candidats[:I_simule]]

    # Récupération du couloir du dernier job effectué par le chauffeur
    couloir_precedent = getattr(p, 'couloir_actuel', None)

    # --- 3. LOGIQUE DE DÉCISION (SI / ALORS) ---

    # RÈGLE 1 : Même couloir ET sur place (Origine = Position actuelle)
    if couloir_precedent:
        for sj in top_n_jobs:
            if get_couloir_id(sj) == couloir_precedent:
                if sj.points_depart[0] == p.position_actuelle:
                    return sj

    # RÈGLE 2 : Sur place uniquement (Origine = Position actuelle)
    for sj in top_n_jobs:
        if sj.points_depart[0] == p.position_actuelle:
            return sj

    # RÈGLE 3 : Proximité de zone (Même groupe/ville)
    # Comparaison des 3 premières lettres du code site
    for sj in top_n_jobs:
        if sj.points_depart[0][:3] == p.position_actuelle[:3]:
            return sj

    # --- 4. RÈGLE FINALE : LE PLUS PROCHE PARMI LE TOP N ---
    # Si aucune règle de proximité immédiate n'est remplie, 
    # on choisit le job qui a la distance d'approche la plus courte.
    
    best_sj_proximite = None
    dist_min = float('inf')

    for sj in top_n_jobs:
        dist_approche = matrice_duree.get(p.position_actuelle, {}).get(sj.points_depart[0], 0)
        
        if dist_approche < dist_min:
            dist_min = dist_approche
            best_sj_proximite = sj
            
    return best_sj_proximite


def simuler_faisabilite(I, liste_sj_type, v_type, matrice_duree, params_logistique, df_vehicules):
    rh = params_logistique.get('rh', {})
    h_start = to_min(rh.get('h_prise_min', 360))
    h_end = to_min(rh.get('h_fin_max', 1380))
    pas = 1 
    
    # --- GESTION DE L'ALÉA CIRCULATION ---
    facteur_alea = 1 + (params_logistique.get('alea_circulation', 0) / 100)
    matrice_travail = {
        origine: {dest: duree * facteur_alea for dest, duree in destinations.items()}
        for origine, destinations in matrice_duree.items()
    }
    
    # --- MARGE INTER-JOBS ---
    marge_interjob = params_logistique.get('marge_interjob', 0)

    try:
        depot_initial = df_vehicules[df_vehicules['Types'] == v_type]['Stationnement initial'].iloc[0]
    except:
        depot_initial = "DEPOT"

    # Initialisation des I véhicules
    postes = [PosteChauffeur(f"{v_type}_{i+1}", v_type, depot_initial, rh) for i in range(I)]
    jobs_restants = list(liste_sj_type)
    minute = h_start

    while minute <= h_end:
        # --- 1. MISE À JOUR DES ÉTATS ---
        for p in postes:
            if p.temps_restant_etat > 0:
                p.temps_restant_etat -= pas
                continue
            
            # Transitions d'états
            if p.etat == 'PRISE_POSTE':
                p.etat = 'DISPONIBLE'
                p.vehicule_deja_affecte = True

            elif p.etat == 'EN_TRAJET_VIDE':
                if p.job_en_cours: # Arrivée pour collecte (approche)
                    p.position_actuelle = p.job_en_cours.points_depart[0]
                    p.etat = 'EN_MISSION'
                    p.temps_restant_etat = p.job_en_cours.poids_total
                    p.enregistrer(minute, "EN_MISSION", p.job_en_cours)
                else: # Arrivée au dépôt (Retour Dépôt fini)
                    p.position_actuelle = p.stationnement_initial
                    temps_travaille = minute - p.h_debut_service_actuel
                    
                    # Correction ici : On check si on doit finir le service ou faire la pause
                    if temps_travaille >= p.amplitude_max - 65:
                        p.etat = 'FIN_DE_SERVICE'
                        p.temps_restant_etat = p.temps_passation
                        p.enregistrer(minute, "PASSATION_FIN")
                    elif not p.pause_faite:
                        p.etat = 'EN_PAUSE'
                        p.temps_restant_etat = p.duree_pause
                        p.enregistrer(minute, "EN_PAUSE")
                    else:
                        p.etat = 'DISPONIBLE'

            elif p.etat == 'EN_MISSION':
                # VÉRIFICATION DEADLINE
                for job_u in p.job_en_cours.liste_jobs:
                    if minute > to_min(job_u.h_deadline):
                        return None
                
                p.position_actuelle = p.job_en_cours.points_arrivee[-1]
                p.couloir_actuel = get_couloir_id(p.job_en_cours)
                
                # Application de la marge inter-job
                p.etat = 'DISPONIBLE'
                p.temps_restant_etat = marge_interjob 
                if marge_interjob > 0:
                    p.enregistrer(minute, "ATTENTE_INTERJOB", details=f"{marge_interjob}min")
                p.job_en_cours = None

            elif p.etat == 'EN_PAUSE':
                p.pause_faite = True
                p.etat = 'DISPONIBLE'
                p.enregistrer(minute, "REPRISE_APRES_PAUSE")

            elif p.etat == 'FIN_DE_SERVICE':
                p.etat = 'INACTIF'
                p.h_debut_service_actuel = None
                p.pause_faite = False
                p.couloir_actuel = None
                p.enregistrer(minute, "VEHICULE_LIBERE")

        # --- 2. AFFECTATION DES JOBS ---
        dispos = [j for j in jobs_restants if to_min(j.h_dispo_min) <= minute]

        for p in postes:
            if p.temps_restant_etat > 0: continue

            # Cas A : Camion libre au dépôt -> Nouveau chauffeur
            if p.etat == 'INACTIF' and dispos:
                p.etat = 'PRISE_POSTE'
                p.temps_restant_etat = 15
                p.h_debut_service_actuel = minute if p.vehicule_deja_affecte else (minute - 15)
                p.enregistrer(minute, "PRISE_POSTE")
                continue

            # Cas B : Chauffeur en service et disponible
            if p.etat == 'DISPONIBLE':
                temps_travaille = minute - p.h_debut_service_actuel
                dist_retour = matrice_travail.get(p.position_actuelle, {}).get(p.stationnement_initial, 30)

                # --- DÉTERMINATION DU BESOIN DE RETOUR ---
                force_retour = False
                if (temps_travaille >= p.amplitude_max / 2 and not p.pause_faite) or (temps_travaille >= p.amplitude_max - 60):
                    force_retour = True

                if force_retour:
                    # On tente de trouver un job qui ramène au dépôt
                    best_sj = selectionner_meilleur_job_retour(
                        p, dispos, minute, matrice_travail, I, 
                        jobs_restants, est_premier_job=(p.couloir_actuel is None)
                    )
                    
                    if best_sj:
                        if (minute + best_sj.poids_total + dist_retour) <= (p.h_debut_service_actuel + p.amplitude_max):
                            affecter_job_avec_matrice(p, best_sj, jobs_restants, dispos, minute, matrice_travail)
                            continue
                    
                    # Sinon, retour à vide
                    p.etat = 'EN_TRAJET_VIDE'
                    p.temps_restant_etat = dist_retour
                    raison = "PAUSE" if not p.pause_faite else "FIN_DE_SERVICE"
                    p.enregistrer(minute, "RETOUR_DEPOT", details=f"Retour pour {raison}")
                    continue

                # --- SÉLECTION STANDARD ---
                if dispos:
                    est_premier = (p.couloir_actuel is None)
                    best_sj = selectionner_meilleur_job(
                        p, dispos, minute, matrice_travail, I, 
                        jobs_restants=jobs_restants, 
                        est_premier_job=est_premier
                    )
                    
                    if best_sj:
                        if (minute + best_sj.poids_total + dist_retour) <= (p.h_debut_service_actuel + p.amplitude_max):
                            affecter_job_avec_matrice(p, best_sj, jobs_restants, dispos, minute, matrice_travail)

        if not jobs_restants: return postes
        minute += pas

    return None

def affecter_job_avec_matrice(p, sj, jobs_restants, dispos, minute, matrice_travail):
    """Version de affecter_job utilisant la matrice avec aléa"""
    dist_approche = matrice_travail.get(p.position_actuelle, {}).get(sj.points_depart[0], 0)
    p.job_en_cours = sj
    jobs_restants.remove(sj)
    if sj in dispos: dispos.remove(sj)
    p.etat = 'EN_TRAJET_VIDE'
    p.temps_restant_etat = dist_approche
    p.enregistrer(minute, "EN_TRAJET_VIDE", sj, "Approche Mission")


def selectionner_meilleur_job_retour(p, dispos, minute, matrice_duree, I_simule, jobs_restants, est_premier_job = False):
    """Variante : Priorise les jobs qui ramènent vers le stationnement initial."""
    # On filtre les jobs qui finissent dans le même "groupe" que le dépôt
    zone_depot = p.stationnement_initial[:3]
    candidats_retour = [sj for sj in dispos if sj.points_arrivee[-1][:3] == zone_depot]
    
    if candidats_retour:
        # Parmi ceux qui rentrent, on prend le plus stressé
        return selectionner_meilleur_job(p, candidats_retour, minute, matrice_duree, I_simule, jobs_restants, est_premier_job)
    
    return None # Si rien ne ramène au dépôt, on rentrera à vide


# =================================================================
# 4. FONCTION D'ENTRÉE PRINCIPALE
# =================================================================

def trouver_meilleure_configuration_journee(liste_sj, n_max_dict, df_vehicules, matrice_duree, params_logistique):
    postes_complets = []
    for v_type, val_max in n_max_dict.items():
        # On utilise le pic d'intensité calculé précédemment comme point de départ
        pic_charge = max(val_max) if isinstance(val_max, list) else val_max
        n_depart = max(1, math.floor(pic_charge * 0.8)) # On tente d'abord avec un peu moins que le pic
        n_limite = math.ceil(pic_charge * 3) # Limite haute de recherche
        
        st.info(f"Analyse **{v_type}** : Tentative d'optimisation des ressources avec **{n_limite}** véhicules max...")
        
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

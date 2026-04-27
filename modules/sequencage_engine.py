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
# 2. CLASSE POSTE CHAUFFEUR
# =================================================================

class PosteChauffeur:
    def __init__(self, id_p, v_type, site_depot, params_rh):
        self.id_poste = id_p
        self.vehicule_type = v_type
        self.stationnement_initial = site_depot
        self.position_actuelle = site_depot
        self.etat = 'INACTIF'  # INACTIF, PRISE_POSTE, DISPONIBLE, EN_TRAJET_VIDE, EN_MISSION, PAUSE, FIN_DE_SERVICE, OPTIMISATION_AM
        self.temps_restant_etat = 0
        self.job_en_cours = None
        self.couloir_actuel = None
        self.h_debut_service_actuel = None 
        self.pause_faite = False
        self.historique = []
        self.amplitude_max = params_rh.get('amplitude_totale', 450) # ex: 9h
        self.duree_pause = params_rh.get('pause', 20)
        self.temps_passation = params_rh.get('temps_fixes_fin', 15)
        self.temps_prise = params_rh.get('temps_fixes_prise', 15)
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
    top_n_jobs = [item['sj'] for item in liste_candidats[:I_simule*2]]

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


def simuler_faisabilite(I_matin, I_am, liste_sj_type, v_type, matrice_duree, params_logistique, df_vehicules):
    rh = params_logistique.get('rh', {})
    h_start = to_min(rh.get('h_prise_min', 360))
    h_end = to_min(rh.get('h_fin_max', 1380))
    # Heure de bascule basée sur l'amplitude légale à partir du début de journée
    h_bascule = h_start + to_min(rh.get('amplitude_totale', 450)) - 1 
    pas = 1 
    
    facteur_alea = 1 + (params_logistique.get('alea_circulation', 0) / 100)
    matrice_travail = {
        origine: {dest: duree * facteur_alea for dest, duree in destinations.items()}
        for origine, destinations in matrice_duree.items()
    }
    
    marge_interjob = params_logistique.get('marge_interjob', 0)
    filtre = df_vehicules[df_vehicules['Types'] == v_type]

    if not filtre.empty:
        depot_initial = filtre['Stationnement initial'].iloc[0]
    else:
        depot_initial = "HSJ"

    # Initialisation avec la flotte complète du matin
    postes = [PosteChauffeur(f"{v_type}_{i+1}", v_type, depot_initial, rh) for i in range(I_matin)]
    jobs_restants = list(liste_sj_type)
    minute = h_start

    while minute <= h_end:
        for p in postes:
            # --- FILTRES DE SÉCURITÉ ---
            if p.etat == 'OPTIMISATION_AM': 
                continue
            
            if p.temps_restant_etat > 0:
                p.temps_restant_etat -= pas
                continue
            
            # --- ÉTAPE 1 : MISE À JOUR DES TRANSITIONS ---
            if p.etat == 'PRISE_POSTE':
                p.etat = 'DISPONIBLE'
                p.vehicule_deja_affecte = True

            elif p.etat == 'EN_TRAJET_VIDE':
                if p.job_en_cours:
                    p.position_actuelle = p.job_en_cours.points_depart[0]
                    p.etat = 'EN_MISSION'
                    p.temps_restant_etat = p.job_en_cours.poids_total
                    p.enregistrer(minute, "EN_MISSION", p.job_en_cours)
                else:
                    p.position_actuelle = p.stationnement_initial
                    temps_travaille = minute - p.h_debut_service_actuel
                    if temps_travaille >= p.amplitude_max - p.temps_passation:
                        p.etat = 'FIN_DE_SERVICE'
                        p.temps_restant_etat = p.temps_passation
                        p.enregistrer(minute, "PASSATION_FIN")
                    elif not p.pause_faite:
                        p.etat = 'EN_PAUSE'
                        p.temps_restant_etat = p.duree_pause
                        p.pause_faite = True
                        p.enregistrer(minute, "EN_PAUSE", details=f"Durée: {p.duree_pause}min")
                    else:
                        p.etat = 'DISPONIBLE'

            elif p.etat == 'EN_MISSION':
                for job_u in p.job_en_cours.liste_jobs:
                    if minute > to_min(job_u.h_deadline):
                        return None # Échec de la configuration
                
                p.position_actuelle = p.job_en_cours.points_arrivee[-1]
                p.couloir_actuel = get_couloir_id(p.job_en_cours)
                p.etat = 'DISPONIBLE'
                p.temps_restant_etat = marge_interjob 
                p.job_en_cours = None

            elif p.etat == 'EN_PAUSE':
                p.etat = 'DISPONIBLE'
                p.enregistrer(minute, "REPRISE_APRES_PAUSE")

            elif p.etat == 'FIN_DE_SERVICE':
                p.etat = 'INACTIF'
                p.h_debut_service_actuel = None
                p.pause_faite = False
                p.couloir_actuel = None
                p.enregistrer(minute, "VEHICULE_LIBERE")

        # --- ÉTAPE 2 : AFFECTATION ET DÉSENGAGEMENT ---
        dispos = [j for j in jobs_restants if to_min(j.h_dispo_min) <= minute]

        for p in postes:
            # 1. On ignore les véhicules déjà libérés définitivement
            if p.etat == 'OPTIMISATION_AM':
                continue
                
            # 2. Si le véhicule est occupé, on passe au suivant
            if p.temps_restant_etat > 0:
                continue
            
            # --- LOGIQUE DE DÉSENGAGEMENT (I AM) PRIORITAIRE ---
            idx_p = int(p.id_poste.split('_')[-1])
            if p.etat == 'DISPONIBLE' and minute >= h_bascule and idx_p > I_am:
                if p.position_actuelle == p.stationnement_initial:
                    p.etat = 'OPTIMISATION_AM'
                    p.temps_restant_etat = 9999 
                    p.enregistrer(minute, "VEHICULE_LIBERE", details="Désengagement (Optimisation AM)")
                else:
                    dist_retour = matrice_travail.get(p.position_actuelle, {}).get(p.stationnement_initial, 30)
                    p.etat = 'EN_TRAJET_VIDE'
                    p.temps_restant_etat = dist_retour
                    p.enregistrer(minute, "RETOUR_DEPOT", details="Retour pour libération AM")
                continue

            # 3. On ignore les inactifs s'il n'y a pas de travail
            if p.etat == 'INACTIF' and not dispos:
                continue

            # --- PROCESSUS D'AFFECTATION STANDARD ---
            if p.etat == 'INACTIF' and dispos:
                p.etat = 'PRISE_POSTE'
                p.temps_restant_etat = p.temps_prise
                p.h_debut_service_actuel = minute if p.vehicule_deja_affecte else (minute - p.temps_prise)
                p.enregistrer(minute, "PRISE_POSTE")
                continue

            if p.etat == 'DISPONIBLE':
                temps_travaille = minute - p.h_debut_service_actuel
                dist_retour_actuel = matrice_travail.get(p.position_actuelle, {}).get(p.stationnement_initial, 30)
                
                besoin_pause_imperatif = (temps_travaille >= 70 and not p.pause_faite)
                besoin_fin = (temps_travaille >= p.amplitude_max - 60)
                limite_critique_pause = 270

                if besoin_fin or besoin_pause_imperatif:
                    best_sj = selectionner_meilleur_job_retour(
                        p, dispos, minute, matrice_travail, len(dispos), 
                        jobs_restants, est_premier_job=(p.couloir_actuel is None), limite_critique=limite_critique_pause
                    )
                    if best_sj:
                        if (minute + best_sj.poids_total + dist_retour_actuel) <= (p.h_debut_service_actuel + p.amplitude_max - p.temps_passation):
                            affecter_job_avec_matrice(p, best_sj, jobs_restants, dispos, minute, matrice_travail)
                            continue
                    
                    if p.position_actuelle != p.stationnement_initial:
                        p.etat = 'EN_TRAJET_VIDE'
                        p.temps_restant_etat = dist_retour_actuel
                        p.enregistrer(minute, "RETOUR_DEPOT", details="Force Impératif")
                    else:
                        p.etat = 'FIN_DE_SERVICE' if besoin_fin else 'EN_PAUSE'
                        p.temps_restant_etat = p.temps_passation if besoin_fin else p.duree_pause
                        if not besoin_fin: p.pause_faite = True
                    continue

                elif dispos:
                    best_sj = selectionner_meilleur_job(p, dispos, minute, matrice_travail, I_matin, jobs_restants)
                    if best_sj:
                        if (minute + best_sj.poids_total + dist_retour_actuel) <= (p.h_debut_service_actuel + p.amplitude_max):
                            affecter_job_avec_matrice(p, best_sj, jobs_restants, dispos, minute, matrice_travail)

        # Condition de sortie : plus de jobs et tout le monde est rentré (ou libéré par l'AM)
        if not jobs_restants and all(p.etat in ['INACTIF', 'FIN_DE_SERVICE', 'OPTIMISATION_AM'] for p in postes):
            return postes
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


def selectionner_meilleur_job_retour(p, dispos, minute, matrice_duree, I_simule, jobs_restants, est_premier_job=False, limite_critique=270):
    """
    Variante : Priorise les jobs qui ramènent vers le stationnement initial 
    ET qui permettent de rentrer avant la limite critique de travail.
    """
    zone_depot = p.stationnement_initial[:3]
    
    # 1. Filtre géographique (Zone du dépôt)
    candidats_zone = [sj for sj in dispos if sj.points_arrivee[-1][:3] == zone_depot]
    
    # 2. Filtre de durée (Approche + Job + Retour < Limite Critique)
    candidats_valides = []
    for sj in candidats_zone:
        dist_approche = matrice_duree.get(p.position_actuelle, {}).get(sj.points_depart[0], 20)
        dist_retour_final = matrice_duree.get(sj.points_arrivee[-1], {}).get(p.stationnement_initial, 20)
        
        # Temps total de travail cumulé à la fin de la boucle (Retour au dépôt inclus)
        temps_total_estime = (minute + dist_approche + sj.poids_total + dist_retour_final) - p.h_debut_service_actuel
        
        if temps_total_estime <= limite_critique:
            candidats_valides.append(sj)

    if candidats_valides:
        # Parmi ceux qui sont géographiquement ET temporellement valides, on prend le plus urgent (stress)
        return selectionner_meilleur_job(p, candidats_valides, minute, matrice_duree, I_simule, jobs_restants, est_premier_job)
    
    return None # Si rien ne ramène au dépôt dans les temps, on rentrera à vide


# =================================================================
# 4. FONCTION D'ENTRÉE PRINCIPALE
# =================================================================

def trouver_meilleure_configuration_journee(liste_sj, n_max_dict, df_vehicules, matrice_duree, params_logistique):
    postes_complets = []
    
    for v_type, val_max in n_max_dict.items():
        # Définition des bornes de recherche
        pic_charge = max(val_max) if isinstance(val_max, list) else val_max
        n_depart = max(1, math.floor(pic_charge * 0.7)) 
        n_limite = math.ceil(pic_charge * 2.5) 
        
        st.info(f"Analyse **{v_type}** : Recherche du couple optimal (Matin / Après-midi)...")
        
        jobs_v = [sj for sj in liste_sj if sj.v_type == v_type]
        if not jobs_v: continue
            
        solution_optimale = None
        
        # --- DOUBLE BOUCLE D'OPTIMISATION ---
        for im in range(n_depart, n_limite + 1):
            for iam in range(1, im + 1):
                res = simuler_faisabilite(im, iam, jobs_v, v_type, matrice_duree, params_logistique, df_vehicules)
                
                if res:
                    st.success(f"✅ **{v_type}** : Trouvé avec {im} (Matin) / {iam} (Après-midi)")
                    solution_optimale = res
                    break 
            
            if solution_optimale:
                postes_complets.extend(solution_optimale)
                break 
        
        if not solution_optimale:
            st.error(f"❌ **{v_type}** : Impossible de trouver un planning même avec {n_limite} véhicules.")

    return {"succes": len(postes_complets) > 0, "postes": postes_complets}

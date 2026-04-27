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
        self.etat = 'INACTIF' 
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
        # Utiliser 0 par défaut si pas de SJ
        poids = sj.poids_total if sj else 0
        
        self.historique.append({
            "Minute_Debut": minute,
            "Heure_Debut": f"{int(minute//60):02d}h{int(minute%60):02d}",
            "Activite": activite,
            "SJ_ID": sj_id,
            "sj_poids": poids, # La clé est maintenant garantie
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


def simuler_faisabilite(I_matin, I_am, prio_tension, liste_sj_type, v_type, matrice_duree, params_logistique, df_vehicules):
    rh = params_logistique.get('rh', {})
    h_start = to_min(rh.get('h_prise_min', 360))
    h_end_global = to_min(rh.get('h_fin_max', 1380)) # Heure limite absolue (ex: 23h)
    h_bascule = h_start + to_min(rh.get('amplitude_totale', 450)) - 1
    marge_interjob = params_logistique.get('marge_interjob', 0)
    
    facteur_alea = 1 + (params_logistique.get('alea_circulation', 0) / 100)
    matrice_travail = {o: {d: dur * facteur_alea for d, dur in dests.items()} for o, dests in matrice_duree.items()}
    
    filtre = df_vehicules[df_vehicules['Types'] == v_type]
    depot_initial = filtre['Stationnement initial'].iloc[0] if not filtre.empty else "HSJ"

    postes = [PosteChauffeur(f"{v_type}_{i+1}", v_type, depot_initial, rh) for i in range(I_matin)]
    jobs_restants = list(liste_sj_type)
    minute = h_start

    # On simule jusqu'à l'heure de fin globale demandée
    while minute <= h_end_global:
        tous_finis = True
        
        for p in postes:
            # 1. GESTION DES ETATS EN COURS
            if p.etat == 'OPTIMISATION_AM': 
                continue # Ce véhicule est libéré pour la journée
            
            tous_finis = False # S'il reste un poste non libéré, on continue
            
            if p.temps_restant_etat > 0:
                p.temps_restant_etat -= 1
                continue
            
            # 2. TRANSITIONS AUTOMATIQUES
            if p.etat == 'PRISE_POSTE':
                p.etat, p.vehicule_deja_affecte = 'DISPONIBLE', True

            elif p.etat == 'EN_TRAJET_VIDE':
                if p.job_en_cours:
                    p.position_actuelle = p.job_en_cours.points_depart[0]
                    p.etat, p.temps_restant_etat = 'EN_MISSION', p.job_en_cours.poids_total
                    p.enregistrer(minute, "EN_MISSION", p.job_en_cours)
                else:
                    p.position_actuelle = p.stationnement_initial
                    # Vérification désengagement AM au retour dépôt
                    idx_p = int(p.id_poste.split('_')[-1])
                    if minute >= h_bascule and idx_p > I_am:
                        p.etat, p.temps_restant_etat = 'PASSATION_AM', p.temps_passation
                        p.enregistrer(minute, "PASSATION_FIN", details="Transition AM")
                    else:
                        p.etat = 'DISPONIBLE'

            elif p.etat == 'EN_MISSION':
                p.position_actuelle = p.job_en_cours.points_arrivee[-1]
                p.couloir_actuel = get_couloir_id(p.job_en_cours)
                p.job_en_cours = None
                if marge_interjob > 0:
                    p.etat, p.temps_restant_etat = 'INTERMISSION', marge_interjob
                    p.enregistrer(minute, "INTERMISSION", details=f"Attente {marge_interjob}min")
                else:
                    p.etat = 'DISPONIBLE'

            elif p.etat == 'INTERMISSION':
                p.etat = 'DISPONIBLE'

            elif p.etat == 'PASSATION_AM' or p.etat == 'FIN_DE_SERVICE':
                # Le poste est réellement terminé ici
                if p.etat == 'PASSATION_AM':
                    p.etat = 'OPTIMISATION_AM'
                    p.enregistrer(minute, "VEHICULE_LIBERE", details="Fin de service AM")
                else:
                    p.etat = 'INACTIF'
                    p.enregistrer(minute, "FIN_DE_SERVICE", details="Repos")
                p.h_debut_service_actuel = None

            # 3. AFFECTATION / LOGIQUE DE FIN
            if p.etat == 'DISPONIBLE':
                dispos = [j for j in jobs_restants if to_min(j.h_dispo_min) <= minute]
                idx_p = int(p.id_poste.split('_')[-1])
                
                # A. Cas Désengagement AM (Prioritaire)
                if minute >= h_bascule and idx_p > I_am:
                    if p.position_actuelle == p.stationnement_initial:
                        p.etat, p.temps_restant_etat = 'PASSATION_AM', p.temps_passation
                        p.enregistrer(minute, "PASSATION_FIN", details="Transition AM")
                    else:
                        p.etat, p.temps_restant_etat = 'EN_TRAJET_VIDE', matrice_travail.get(p.position_actuelle, {}).get(p.stationnement_initial, 30)
                        p.enregistrer(minute, "RETOUR_DEPOT", details="Retour pour libération")
                    continue

                # B. Recherche de mission
                if dispos:
                    nb_Jobs = max(math.ceil(prio_tension * len(dispos)), 1)
                    best_sj = selectionner_meilleur_job(p, dispos, minute, matrice_travail, nb_Jobs, jobs_restants)
                    if best_sj:
                        # On vérifie si ça rentre dans l'amplitude
                        dist_ret = matrice_travail.get(best_sj.points_arrivee[-1], {}).get(p.stationnement_initial, 30)
                        if (minute + best_sj.poids_total + dist_ret) <= (p.h_debut_service_actuel + p.amplitude_max):
                            affecter_job_avec_matrice(p, best_sj, jobs_restants, dispos, minute, matrice_travail)
                            continue
                
                # C. Si plus rien à faire, on complète simplement avec du DISPONIBLE (le temps passe via la boucle while)
                # On pourrait aussi forcer un retour dépôt si minute s'approche de la fin de poste
                temps_trav = minute - p.h_debut_service_actuel
                if temps_trav >= p.amplitude_max - p.temps_passation:
                    if p.position_actuelle != p.stationnement_initial:
                        p.etat, p.temps_restant_etat = 'EN_TRAJET_VIDE', matrice_travail.get(p.position_actuelle, {}).get(p.stationnement_initial, 30)
                        p.enregistrer(minute, "RETOUR_DEPOT", details="Fin de journée")
                    else:
                        p.etat, p.temps_restant_etat = 'FIN_DE_SERVICE', p.temps_passation
                        p.enregistrer(minute, "PASSATION_FIN")

            # Cas spécifique : Initialisation d'un poste INACTIF s'il y a du travail
            if p.etat == 'INACTIF' and jobs_restants:
                dispos = [j for j in jobs_restants if to_min(j.h_dispo_min) <= minute]
                if dispos:
                    p.etat, p.temps_restant_etat = 'PRISE_POSTE', p.temps_prise
                    p.h_debut_service_actuel = minute
                    p.enregistrer(minute, "PRISE_POSTE")

        if tous_finis and not jobs_restants:
            break
            
        minute += 1

    return postes


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
    tensions_test = [0.2, 0.4, 0.6, 0.8, 1.0]
    
    for v_type, val_max in n_max_dict.items():
        pic_charge = max(val_max) if isinstance(val_max, list) else val_max
        n_depart, n_limite = max(1, math.floor(pic_charge * 0.5)), math.ceil(pic_charge * 2.5)
        jobs_v = [sj for sj in liste_sj if sj.v_type == v_type]
        if not jobs_v: continue
            
        meilleure_sol, min_v_total, max_occ = None, float('inf'), -1

        for tension in tensions_test:
            for im in range(n_depart, n_limite + 1):
                if im > min_v_total: break
                for iam in range(1, im + 1):
                    res = simuler_faisabilite(im, iam, tension, jobs_v, v_type, matrice_duree, params_logistique, df_vehicules)
                    if res:
                        trav_utile, ampl_conso = 0, 0
                        for p in res:
                            if p.historique:
                                ampl_conso += (p.historique[-1]['Minute_Debut'] - p.historique[0]['Minute_Debut'])
                                for h in p.historique:
                                    if h['Activite'] == 'EN_MISSION': trav_utile += h['sj_poids']
                                    elif 'TRAJET_VIDE' in h['Activite'] or 'RETOUR' in h['Activite']: trav_utile += 15
                        
                        taux_occ = trav_utile / max(ampl_conso, 1)
                        if (im + iam) < min_v_total or ((im + iam) == min_v_total and taux_occ > max_occ):
                            min_v_total, max_occ, meilleure_sol = (im + iam), taux_occ, res
                        break
                if meilleure_sol and (im + 1) > min_v_total: break

        if meilleure_sol:
            st.success(f"✅ **{v_type}** : Optimisé avec Taux Occ: {max_occ:.1%}")
            postes_complets.extend(meilleure_sol)
        else: st.error(f"❌ **{v_type}** : Échec.")

    return {"succes": len(postes_complets) > 0, "postes": postes_complets}

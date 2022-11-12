import argparse
import importlib
from operator import le
import os
import sys

import numpy as np
import torch as th
import yaml
from huggingface_sb3 import EnvironmentName
from stable_baselines3.common.utils import set_random_seed

import utils.import_envs  # noqa: F401 pylint: disable=unused-import
from utils import ALGOS, create_test_env, get_saved_hyperparams
from utils.exp_manager import ExperimentManager
from utils.load_from_hub import download_from_hub
from utils.utils import StoreDict, get_model_path


def main():  # noqa: C901
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", help="environment ID", type=EnvironmentName, default="CartPole-v1")
    parser.add_argument("-f", "--folder", help="Log folder", type=str, default="rl-trained-agents")
    parser.add_argument("--algo", help="RL Algorithm", default="ppo", type=str, required=False, choices=list(ALGOS.keys()))
    parser.add_argument("-n", "--n-timesteps", help="number of timesteps", default=1000, type=int)
    parser.add_argument("--num-threads", help="Number of threads for PyTorch (-1 to use default)", default=-1, type=int)
    parser.add_argument("--n-envs", help="number of environments", default=1, type=int)
    parser.add_argument("--exp-id", help="Experiment ID (default: 0: latest, -1: no exp folder)", default=0, type=int)
    parser.add_argument("--verbose", help="Verbose mode (0: no output, 1: INFO)", default=1, type=int)
    parser.add_argument(
        "--no-render", action="store_true", default=False, help="Do not render the environment (useful for tests)"
    )
    parser.add_argument("--deterministic", action="store_true", default=False, help="Use deterministic actions")
    parser.add_argument("--device", help="PyTorch device to be use (ex: cpu, cuda...)", default="auto", type=str)
    parser.add_argument(
        "--load-best", action="store_true", default=False, help="Load best model instead of last model if available"
    )
    parser.add_argument(
        "--load-checkpoint",
        type=int,
        help="Load checkpoint instead of last model if available, "
        "you must pass the number of timesteps corresponding to it",
    )
    parser.add_argument(
        "--load-last-checkpoint",
        action="store_true",
        default=False,
        help="Load last checkpoint instead of last model if available",
    )
    parser.add_argument("--stochastic", action="store_true", default=False, help="Use stochastic actions")
    parser.add_argument(
        "--norm-reward", action="store_true", default=False, help="Normalize reward if applicable (trained with VecNormalize)"
    )
    parser.add_argument("--seed", help="Random generator seed", type=int, default=0)
    parser.add_argument("--reward-log", help="Where to log reward", default="", type=str)
    parser.add_argument(
        "--gym-packages",
        type=str,
        nargs="+",
        default=[],
        help="Additional external Gym environment package modules to import (e.g. gym_minigrid)",
    )
    parser.add_argument(
        "--env-kwargs", type=str, nargs="+", action=StoreDict, help="Optional keyword argument to pass to the env constructor"
    )
    parser.add_argument(
        "--custom-objects", action="store_true", default=False, help="Use custom objects to solve loading issues"
    )

    args = parser.parse_args()

    # Going through custom gym packages to let them register in the global registory
    for env_module in args.gym_packages:
        importlib.import_module(env_module)

    env_name: EnvironmentName = args.env
    algo = args.algo
    folder = args.folder

    final_reward = []
    final_std = []
    final_values = []
    name = 'car_racing_no_mirror.txt'
    for i in range(1, 2):
        global_reward = []
        global_std = []
        global_values = []
        with open(name, "a") as f:
            f.write(f'======== Agent {i} ========\n')
        for j in range(1, 2):
            # args.load_checkpoint = 500000*j
            args.seed = j
            args.exp_id = i
            print(f'======== Agent {i} Checkpoint {500000*j} ========')
            try:
                _, model_path, log_path = get_model_path(
                    args.exp_id,
                    folder,
                    algo,
                    env_name,
                    args.load_best,
                    args.load_checkpoint,
                    args.load_last_checkpoint,
                )
            except (AssertionError, ValueError) as e:
                # Special case for rl-trained agents
                # auto-download from the hub
                if "rl-trained-agents" not in folder:
                    raise e
                else:
                    print("Pretrained model not found, trying to download it from sb3 Huggingface hub: https://huggingface.co/sb3")
                    # Auto-download
                    download_from_hub(
                        algo=algo,
                        env_name=env_name,
                        exp_id=args.exp_id,
                        folder=folder,
                        # organization="sb3",
                        organization="meln1k",
                        repo_name=None,
                        force=False,
                    )
                    # Try again
                    _, model_path, log_path = get_model_path(
                        args.exp_id,
                        folder,
                        algo,
                        env_name,
                        args.load_best,
                        args.load_checkpoint,
                        args.load_last_checkpoint,
                    )

            print(f"Loading {model_path}")

            # Off-policy algorithm only support one env for now
            off_policy_algos = ["qrdqn", "dqn", "ddpg", "sac", "her", "td3", "tqc"]

            if algo in off_policy_algos:
                args.n_envs = 1

            set_random_seed(args.seed)

            if args.num_threads > 0:
                if args.verbose > 1:
                    print(f"Setting torch.num_threads to {args.num_threads}")
                th.set_num_threads(args.num_threads)

            is_atari = ExperimentManager.is_atari(env_name.gym_id)

            stats_path = os.path.join(log_path, env_name)
            hyperparams, stats_path = get_saved_hyperparams(stats_path, norm_reward=args.norm_reward, test_mode=True)

            # load env_kwargs if existing
            env_kwargs = {}
            args_path = os.path.join(log_path, env_name, "args.yml")
            if os.path.isfile(args_path):
                with open(args_path) as f:
                    loaded_args = yaml.load(f, Loader=yaml.UnsafeLoader)  # pytype: disable=module-attr
                    if loaded_args["env_kwargs"] is not None:
                        env_kwargs = loaded_args["env_kwargs"]
            # overwrite with command line arguments
            if args.env_kwargs is not None:
                env_kwargs.update(args.env_kwargs)

            log_dir = args.reward_log if args.reward_log != "" else None

            env = create_test_env(
                env_name.gym_id,
                n_envs=args.n_envs,
                stats_path=stats_path,
                seed=args.seed,
                log_dir=log_dir,
                should_render=not args.no_render,
                hyperparams=hyperparams,
                env_kwargs=env_kwargs,
            )

            kwargs = dict(seed=args.seed)
            if algo in off_policy_algos:
                # Dummy buffer size as we don't need memory to enjoy the trained agent
                kwargs.update(dict(buffer_size=1))
                # Hack due to breaking change in v1.6
                # handle_timeout_termination cannot be at the same time
                # with optimize_memory_usage
                if "optimize_memory_usage" in hyperparams:
                    kwargs.update(optimize_memory_usage=False)

            # Check if we are running python 3.8+
            # we need to patch saved model under python 3.6/3.7 to load them
            newer_python_version = sys.version_info.major == 3 and sys.version_info.minor >= 8

            custom_objects = {}
            if newer_python_version or args.custom_objects:
                custom_objects = {
                    "learning_rate": 0.0,
                    "lr_schedule": lambda _: 0.0,
                    "clip_range": lambda _: 0.0,
                }

            model = ALGOS[algo].load(model_path, env=env, custom_objects=custom_objects, device=args.device, **kwargs)

            obs = env.reset()

            # Deterministic by default except for atari games
            stochastic = args.stochastic or is_atari and not args.deterministic
            deterministic = not stochastic

            episode_reward = 0.0
            episode_rewards, episode_lengths = [], []
            episode_value = []
            ep_len = 0
            value = 0
            # For HER, monitor success rate
            successes = []
            lstm_states = None
            episode_start = np.ones((env.num_envs,), dtype=bool)
            cnt = 0
            try:
                while len(episode_rewards) < args.n_timesteps and cnt < 5:
                    action, lstm_states = model.predict(
                        obs,
                        state=lstm_states,
                        episode_start=episode_start,
                        deterministic=deterministic,
                    )
                    # print(obs)
                    # print(obs.shape)
                    # print(len(episode_rewards))
                    # cnt+=1

                    # Bipedal
                    # mask = np.array([-1,-1,-1,-1,-1,-1,-1,-1,1,-1,-1,-1,-1,1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1])
                    # mirror_obs = obs * mask
                    # temp = np.array(mirror_obs[0][9:14], copy=True)
                    # mirror_obs[0][9:14] = mirror_obs[0][4:9]
                    # mirror_obs[0][4:9] = temp

                    # mirror_obs2 = np.array(obs, copy=True)
                    # temp = np.array(mirror_obs2[0][9:14], copy=True)
                    # mirror_obs2[0][9:14] = mirror_obs2[0][4:9]
                    # mirror_obs2[0][4:9] = temp
                    
                    # val_obs = model.forward(obs)
                    # val_mirror_obs = model.forward(mirror_obs)
                    # val_mirror_obs2 = model.forward(mirror_obs2)
                    # if val_obs < val_mirror_obs and val_mirror_obs2 < val_mirror_obs:
                    #     action, lstm_states = model.predict(
                    #         mirror_obs,
                    #         state=lstm_states,
                    #         episode_start=episode_start,
                    #         deterministic=deterministic,
                    #     )
                    #     action = np.array(action) * -1
                    #     # temp = np.array(action[0][2:4], copy=True)
                    #     # action[0][2:4] = action[0][0:2]
                    #     # action[0][0:2] = temp
                    #     value += val_mirror_obs.detach().numpy()
                    # elif val_obs < val_mirror_obs2:
                    #     action, lstm_states = model.predict(
                    #         mirror_obs2,
                    #         state=lstm_states,
                    #         episode_start=episode_start,
                    #         deterministic=deterministic,
                    #     )
                    #     temp = np.array(action[0][2:4], copy=True)
                    #     action[0][2:4] = action[0][0:2]
                    #     action[0][0:2] = temp
                    # else:
                    #     value += val_obs.detach().numpy()
                    
                    # val_obs = model.forward(obs)
                    # value += val_obs.detach().numpy()
                    print(action)

                    obs, reward, done, infos = env.step(action)

                    episode_start = done

                    if not args.no_render:
                        env.render("human")

                    episode_reward += reward[0]
                    ep_len += 1

                    if args.n_envs == 1:
                        # For atari the return reward is not the atari score
                        # so we have to get it from the infos dict
                        if is_atari and infos is not None and args.verbose >= 1:
                            episode_infos = infos[0].get("episode")
                            if episode_infos is not None:
                                print(f"Atari Episode Score: {episode_infos['r']:.2f}")
                                print("Atari Episode Length", episode_infos["l"])

                        if done and not is_atari and args.verbose > 0:                   
                            # if len(episode_rewards)%100 == 0 :
                            #     print(f'=============== Model: {len(episode_rewards)} ===============')
                            # NOTE: for env using VecNormalize, the mean reward
                            # is a normalized reward when `--norm_reward` flag is passed
                            print(f"Episode Reward: {episode_reward:.2f}")
                            print("Episode Length", ep_len)
                            episode_rewards.append(episode_reward)
                            episode_lengths.append(ep_len)
                            episode_value.append(value)
                            if ep_len == 500:
                                successes.append(1/args.n_timesteps)
                            episode_reward = 0.0
                            ep_len = 0
                            value = 0

                        # Reset also when the goal is achieved when using HER
                        if done and infos[0].get("is_success") is not None:
                            if args.verbose > 1:
                                print("Success?", infos[0].get("is_success", False))

                            if infos[0].get("is_success") is not None:
                                successes.append(infos[0].get("is_success", False))
                                episode_reward, ep_len = 0.0, 0

            except KeyboardInterrupt:
                pass

            if args.verbose > 0 and len(successes) > 0:
                print(f"Success rate: {np.sum(successes):.2f}%")

            if args.verbose > 0 and len(episode_rewards) > 0:
                print(f"{len(episode_rewards)} Episodes")
                print(f"Mean reward: {np.mean(episode_rewards):.2f} +/- {np.std(episode_rewards):.2f}")
                global_reward.append(np.mean(episode_rewards))
                global_std.append(np.std(episode_rewards))
                global_values.append(np.mean(episode_value))

            if args.verbose > 0 and len(episode_lengths) > 0:
                print(f"Mean episode length: {np.mean(episode_lengths):.2f} +/- {np.std(episode_lengths):.2f}")

            env.close()

        print(f"Global reward: {np.mean(global_reward):.2f} +/- {np.std(global_reward):.2f}")
        print(global_reward)
        print(f"Global std: {np.mean(global_std):.2f} +/- {np.std(global_std):.2f}")
        print(global_std)
        print(f"Global values: {np.mean(global_values):.2f} +/- {np.std(global_values):.2f}")
        print(global_values)
        
        with open(name, "a") as f:
            f.write(f"Global reward: {np.mean(global_reward):.2f} +/- {np.std(global_reward):.2f}\n")
            f.write(f"[{', '.join(map(str, global_reward))}]\n")
            f.write(f"Global std: {np.mean(global_std):.2f} +/- {np.std(global_std):.2f}\n")
            f.write(f"[{', '.join(map(str, global_std))}]\n")
            f.write(f"Global values: {np.mean(global_values):.2f} +/- {np.std(global_values):.2f}\n")
            f.write(f"[{', '.join(map(str, global_values))}]\n")

        if final_reward == []:
            final_reward = np.array(global_reward)
            final_std = np.array(global_std)
            final_values = np.array(global_values)
        else:
            final_reward += np.array(global_reward)
            final_std += np.array(global_std)
            final_values += np.array(global_values)

    final_reward = np.array(final_reward)/10
    final_std = np.array(final_std)/10
    final_values = np.array(final_values)/10

    print(f"Final reward: {np.mean(final_reward):.2f} +/- {np.std(final_reward):.2f}\n")
    print(final_reward)
    print(f"Final std: {np.mean(final_std):.2f} +/- {np.std(final_std):.2f}\n")
    print(final_std)
    print(f"Final global: {np.mean(final_values):.2f} +/- {np.std(final_values):.2f}\n")
    print(final_values)

    with open(name, "a") as f:
        f.write(f"Final reward: {np.mean(final_reward):.2f} +/- {np.std(final_reward):.2f}\n")
        f.write(f"[{', '.join(map(str, final_reward))}]\n")
        f.write(f"Final std: {np.mean(final_std):.2f} +/- {np.std(final_std):.2f}\n")
        f.write(f"[{', '.join(map(str, final_std))}]\n")
        f.write(f"Final global: {np.mean(final_values):.2f} +/- {np.std(final_values):.2f}\n")
        f.write(f"[{', '.join(map(str, final_values))}]\n")

if __name__ == "__main__":
    main()

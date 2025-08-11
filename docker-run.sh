#!/bin/bash

# JAV Metadata Updater - Docker运行脚本
# 使用说明：./docker-run.sh [选项]

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 打印彩色消息
print_message() {
    echo -e "${2}${1}${NC}"
}

# 检查配置文件
check_config() {
    if [ ! -f "config.yaml" ]; then
        print_message "❌ 未找到 config.yaml 文件" $RED
        print_message "请先复制 config-sample.yaml 为 config.yaml 并填入配置" $YELLOW
        
        if [ -f "config-sample.yaml" ]; then
            print_message "正在为您创建配置模板..." $BLUE
            cp config-sample.yaml config.yaml
            print_message "✅ 已创建 config.yaml，请编辑此文件填入您的配置" $GREEN
        fi
        exit 1
    fi
}

# 创建必要目录
create_dirs() {
    mkdir -p logs
    print_message "✅ 创建日志目录：logs/" $GREEN
}

# 构建镜像
build_image() {
    print_message "🔨 正在构建 Docker 镜像..." $BLUE
    docker build -t javplex:latest .
    print_message "✅ Docker 镜像构建完成" $GREEN
}

# 显示帮助信息
show_help() {
    echo "JAV Metadata Updater Docker 运行脚本"
    echo ""
    echo "用法："
    echo "  $0 [选项]"
    echo ""
    echo "选项："
    echo "  -h, --help          显示此帮助信息"
    echo "  -b, --build         构建 Docker 镜像"
    echo "  -r, --run           交互式运行（默认）"
    echo "  -d, --daemon        后台运行"
    echo "  -l, --logs          查看日志"
    echo "  -s, --stop          停止容器"
    echo "  --dry-run           测试模式运行"
    echo "  --limit N           限制处理视频数量"
    echo ""
    echo "示例："
    echo "  $0                  # 交互式运行"
    echo "  $0 -d               # 后台运行"
    echo "  $0 --dry-run        # 测试模式"
    echo "  $0 --limit 10       # 只处理前10个视频"
    echo "  $0 -l               # 查看运行日志"
}

# 交互式运行
run_interactive() {
    print_message "🚀 启动交互式运行..." $BLUE
    docker run -it --rm \
        --name jav-updater-interactive \
        -v "$(pwd)/config.yaml:/app/config.yaml:ro" \
        -v "$(pwd)/logs:/app/logs" \
        javplex:latest "$@"
}

# 后台运行
run_daemon() {
    print_message "🚀 启动后台运行..." $BLUE
    docker run -d \
        --name jav-updater-daemon \
        -v "$(pwd)/config.yaml:/app/config.yaml:ro" \
        -v "$(pwd)/logs:/app/logs" \
        javplex:latest "$@"
    
    print_message "✅ 容器已在后台启动" $GREEN
    print_message "使用以下命令查看日志：" $YELLOW
    echo "  docker logs -f jav-updater-daemon"
    echo "  或者：$0 -l"
}

# 查看日志
show_logs() {
    if docker ps -q -f name=jav-updater-daemon | grep -q .; then
        print_message "📋 显示实时日志 (Ctrl+C 退出):" $BLUE
        docker logs -f jav-updater-daemon
    elif [ -f "logs/jav_meta_updater.log" ]; then
        print_message "📋 显示本地日志文件:" $BLUE
        tail -f logs/jav_meta_updater.log
    else
        print_message "❌ 未找到运行中的容器或日志文件" $RED
    fi
}

# 停止容器
stop_container() {
    print_message "🛑 停止容器..." $YELLOW
    docker stop jav-updater-daemon 2>/dev/null || true
    docker rm jav-updater-daemon 2>/dev/null || true
    print_message "✅ 容器已停止" $GREEN
}

# 主逻辑
main() {
    # 检查 Docker 是否可用
    if ! command -v docker &> /dev/null; then
        print_message "❌ Docker 未安装或不可用" $RED
        exit 1
    fi
    
    # 解析参数
    case "${1:-}" in
        -h|--help)
            show_help
            exit 0
            ;;
        -b|--build)
            check_config
            create_dirs
            build_image
            exit 0
            ;;
        -d|--daemon)
            shift
            check_config
            create_dirs
            build_image
            stop_container
            run_daemon "$@"
            exit 0
            ;;
        -l|--logs)
            show_logs
            exit 0
            ;;
        -s|--stop)
            stop_container
            exit 0
            ;;
        -r|--run|"")
            # 默认交互式运行
            shift 2>/dev/null || shift 0
            check_config
            create_dirs
            build_image
            run_interactive "$@"
            exit 0
            ;;
        *)
            # 传递所有参数给程序
            check_config
            create_dirs
            build_image
            run_interactive "$@"
            exit 0
            ;;
    esac
}

# 运行主函数
main "$@"